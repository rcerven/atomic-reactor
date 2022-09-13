"""
Copyright (c) 2022 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""
import contextlib
import functools
import os
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional
from json import JSONDecodeError

from osbs.utils import ImageName

from atomic_reactor import dirs
from atomic_reactor import util
from atomic_reactor.tasks.common import Task, TaskParams
from atomic_reactor.utils import retries
from atomic_reactor.utils import remote_host


logger = logging.getLogger(__name__)


class BuildTaskError(Exception):
    """The build task failed."""


class BuildProcessError(BuildTaskError):
    """The subprocess that was supposed to build the image failed."""


class ExceedsImageSizeError(RuntimeError):
    """Error of exceeding image size"""


class PushError(BuildTaskError):
    """Failed to push the built image."""


class InspectError(BuildTaskError):
    """Failed to inspect the built image."""


@dataclass(frozen=True)
class BinaryBuildTaskParams(TaskParams):
    """Binary container build task parameters"""
    platform: str


class BinaryBuildTask(Task[BinaryBuildTaskParams]):
    """Binary container build task."""

    # Build tasks run in parallel, saving context data would lead to race conditions
    #   and inconsistencies. Luckily, this task does not make any context data updates.
    autosave_context_data = False

    def execute(self) -> None:
        """Build a container image for the platform specified in the task parameters.

        The built image will be pushed to the unique tag for this platform, which can be found
        in tag_conf.get_unique_images_with_platform() (where tag_conf comes from context data).
        """
        platform = self._params.platform

        enabled_platforms = util.get_platforms(self.workflow_data)
        flatpak = self._params.user_params.get('flatpak', False)

        if platform not in enabled_platforms:
            logger.info(
                r"Platform %s is not enabled for this build (enabled platforms: %s). Exiting.",
                platform,
                enabled_platforms,
            )
            return

        config = self.load_config()
        build_dir = self.get_build_dir().platform_dir(platform)
        dest_tag = self.workflow_data.tag_conf.get_unique_images_with_platform(platform)[0]

        logger.info("Building for the %s platform from %s", platform, build_dir.dockerfile_path)

        with contextlib.ExitStack() as defer:
            defer.callback(
                logger.info, "Dockerfile used for build:\n%s", build_dir.dockerfile_path.read_text()
            )
            build_log_file = defer.enter_context(
                open(self.get_context_dir().get_platform_build_log(platform), 'w+')
            )

            remote_resource = self.acquire_remote_resource(config.remote_hosts)
            defer.callback(remote_resource.unlock)

            podman_remote = PodmanRemote.setup_for(
                remote_resource, registries_authfile=get_authfile_path(config.registry)
            )

            logger.info('=======================build dir')
            logger.info(build_dir.path)
            logger.info(os.listdir(build_dir.path))
            for filename in os.listdir(build_dir.path):
                if not filename.endswith('json'):
                    continue

                out_file_path = build_dir.path / filename
                logger.info('=======================filename')
                logger.info(out_file_path)
                logger.info('=======================size')
                logger.info(os.path.getsize(out_file_path))
                logger.info('=======================mtime')
                logger.info(os.path.getmtime(out_file_path))
                logger.info('=======================ctim')
                logger.info(os.path.getctime(out_file_path))

            # log the image+host for auditing purposes
            logger.info("Building image=%s on host=%s", dest_tag, remote_resource.host.hostname)

            output_lines = podman_remote.build_container(
                build_dir=build_dir,
                build_args=self.workflow_data.buildargs,
                dest_tag=dest_tag,
                flatpak=flatpak,
            )
            for line in output_lines:
                logger.info(line.rstrip())
                build_log_file.write(line)

            logger.info("Build finished successfully! Pushing image to %s", dest_tag)

            image_size_limit = config.image_size_limit['binary_image']
            image_size = podman_remote.get_image_size(dest_tag)

            if image_size > image_size_limit:
                raise ExceedsImageSizeError(
                    'The size {} of image {} exceeds the limitation {} '
                    'configured in reactor config.'
                    .format(image_size, dest_tag, image_size_limit)
                )

            podman_remote.push_container(dest_tag, insecure=config.registry.get("insecure", False))

    def acquire_remote_resource(self, remote_hosts_config: dict) -> remote_host.LockedResource:
        """Lock a build slot on a remote host."""
        logger.info("Acquiring a build slot on a remote host")
        pool = remote_host.RemoteHostsPool.from_config(remote_hosts_config, self._params.platform)
        resource = pool.lock_resource(prid=self._params.pipeline_run_name)
        if not resource:
            raise BuildTaskError(
                "Failed to acquire a build slot on any remote host! See the logs for more details."
            )
        return resource


def get_authfile_path(registry_config: Dict[str, Any]) -> Optional[str]:
    """Get the authentication file path (if any) for the registry."""
    if secret_path := registry_config.get("secret"):
        return util.Dockercfg(secret_path).json_secret_path
    return None


@functools.lru_cache
def which_podman() -> str:
    """Return the full path to the podman or podman-remote executable. Prefer podman."""
    podman = shutil.which("podman") or shutil.which("podman-remote")
    if not podman:
        raise BuildTaskError("Could not find either podman or podman-remote in $PATH!")
    return podman


class PodmanRemote:
    """Wrapper for running podman --remote commands on a remote host.

    Works with both podman-remote and full podman.
    """

    def __init__(self, connection_name: str, registries_authfile: Optional[str] = None):
        """Initialize a PodmanRemote instance.

        :param connection_name: The name of an existing podman-remote connection
        :param registries_authfile: The path to a JSON file containing authentication (if needed)
            for all the registries that are relevant to this build (pulling base images, pushing
            the built image). See `man containers-auth.json` for the expected format.
        """
        self._connection_name = connection_name
        self._registries_authfile = registries_authfile

    @property
    def _podman_remote_cmd(self) -> List[str]:
        return [which_podman(), "--remote", f"--connection={self._connection_name}"]

    @classmethod
    def setup_for(
        cls,
        remote_resource: remote_host.LockedResource,
        *,
        registries_authfile: Optional[str] = None,
    ):
        """Set up a connection for the specified remote resource, return a PodmanRemote instance."""
        # identify the connection by the pipelineRun name and build platform
        connection_name = f'{remote_resource.prid}-{remote_resource.host_platform}'
        host = remote_resource.host
        cmd = [
            which_podman(),
            "system",
            "connection",
            "add",
            f"--identity={host.ssh_keyfile}",
            f"--socket-path={host.socket_path}",
            connection_name,
            f"ssh://{host.username}@{host.hostname}",
        ]
        logger.debug("Running %s", " ".join(cmd))

        try:
            subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            raise BuildTaskError(
                f"Failed to set up podman-remote connection: {e.output.decode()}"
            ) from e

        return cls(connection_name, registries_authfile=registries_authfile)

    def build_container(
        self,
        *,
        build_dir: dirs.BuildDir,
        build_args: Dict[str, str],
        dest_tag: ImageName,
        flatpak: bool,
    ) -> Iterator[str]:
        """Build a container image from the specified build directory.

        Pass the specified build arguments as ARG values using --build-arg.

        The built image will be available locally on the machine that built it, tagged with
        the specified dest_tag. This method does not specify the format of the built image
        (nor does the format really matter), but podman will typically default to 'oci'.

        This method returns an iterator which yields individual lines from the stdout
        and stderr of the build process as they become available.
        """
        options = [
            f"--tag={dest_tag}",
            "--no-cache",  # make sure the build uses a clean environment
            "--pull-always",  # as above
        ]
        if flatpak:
            options.append("--squash-all")
            for device in ['null', 'random', 'urandom', 'zero']:
                options.append(f"--device=/dev/{device}:/var/tmp/flatpak-build/dev/{device}")
        else:
            options.append("--squash")
        options.extend(f"--build-arg={key}={value}" for key, value in build_args.items())
        if self._registries_authfile:
            # TBD: this only works if the OSBS deployment uses a single registry secret
            # TBD: we also can't properly handle "insecure" config for pull registries
            options.append(f"--authfile={self._registries_authfile}")

        build_cmd = [*self._podman_remote_cmd, "build", *options, str(build_dir.path)]

        logger.debug("Running %s", " ".join(build_cmd))

        build_process = subprocess.Popen(
            build_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
        )

        # passing stdout=PIPE guarantees that stdout is not None, but the type hints for the
        #   subprocess module do not express that (TL;DR - this is just for type checkers)
        assert build_process.stdout is not None

        last_line = None

        while True:
            if line := build_process.stdout.readline():
                yield line
                # keep the last non-empty, non-whitespace line for an eventual error message
                last_line = line.rstrip() or last_line

            if (rc := build_process.poll()) is not None:
                break

        if rc != 0:
            error = last_line if last_line else "<no output!>"
            raise BuildProcessError(f"Build failed (rc={rc}): {error}")

    def get_image_size(self, dest_tag: ImageName) -> int:
        inspect_cmd = [*self._podman_remote_cmd, 'image', 'inspect', str(dest_tag)]
        try:
            output = retries.run_cmd(inspect_cmd)
            inspect_json = json.loads(output)
            image_inspect = inspect_json[0]
            image_size = image_inspect['Size']
        except subprocess.CalledProcessError as e:
            raise InspectError(f"Couldn't check image size for image:{str(dest_tag)}."
                               f" (rc={e.returncode})") from e
        except IndexError as e:
            raise InspectError("Image inspect didn't return any results for image:"
                               f"{str(dest_tag)}") from e
        except JSONDecodeError as e:
            raise InspectError("Image inspect returned invalid JSON for image:"
                               f"{str(dest_tag)}") from e
        return image_size

    def push_container(self, dest_tag: ImageName, *, insecure: bool = False) -> None:
        """Push the built container (named dest_tag) to the registry (as dest_tag).

        Push the container as v2s2 (Docker v2 schema 2) regardless of the original format.

        :param dest_tag: the name of the built container, and the destination for the push
        :param insecure: disable --tls-verify?
        """
        options = ["--format=v2s2"]
        if self._registries_authfile:
            options.append(f"--authfile={self._registries_authfile}")
        if insecure:
            options.append("--tls-verify=false")

        push_cmd = [*self._podman_remote_cmd, "push", *options, str(dest_tag)]

        try:
            retries.run_cmd(push_cmd)
        except subprocess.CalledProcessError as e:
            raise PushError(
                f"Push failed (rc={e.returncode}). Check the logs for more details."
            ) from e
