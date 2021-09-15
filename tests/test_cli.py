"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

import logging
import os
import sys
import codecs
import encodings

import pytest
import flexmock

from atomic_reactor.buildimage import BuildImageBuilder
from atomic_reactor.core import DockerTasker
from atomic_reactor.plugin import InputPluginsRunner
import atomic_reactor.cli.main
from atomic_reactor.constants import BUILD_JSON_ENV

from tests.util import uuid_value
from tests.constants import LOCALHOST_REGISTRY, DOCKERFILE_GIT, DOCKERFILE_OK_PATH, FILES, MOCK

if MOCK:
    from tests.docker_mock import mock_docker

PRIV_BUILD_IMAGE = uuid_value()
DH_BUILD_IMAGE = uuid_value()


logger = logging.getLogger('atomic_reactor.tests')

if MOCK:
    mock_docker()
dt = DockerTasker()
reactor_root = os.path.dirname(os.path.dirname(__file__))

with_all_sources = pytest.mark.parametrize('source_provider, uri', [
    ('git', DOCKERFILE_GIT),
    ('path', DOCKERFILE_OK_PATH),
])

# TEST-SUITE SETUP


def setup_module(module):
    if MOCK:
        return

    b = BuildImageBuilder(reactor_local_path=reactor_root)
    b.create_image(os.path.join(reactor_root, 'images', 'privileged-builder'),
                   PRIV_BUILD_IMAGE, use_cache=True)

    b2 = BuildImageBuilder(reactor_local_path=reactor_root)
    b2.create_image(os.path.join(reactor_root, 'images', 'dockerhost-builder'),
                    DH_BUILD_IMAGE, use_cache=True)


def teardown_module(module):
    if MOCK:
        return
    dt.remove_image(PRIV_BUILD_IMAGE, force=True)
    dt.remove_image(DH_BUILD_IMAGE, force=True)


# TESTS

class TestCLISuite(object):

    def exec_cli(self, command):
        saved_args = sys.argv
        sys.argv = command
        atomic_reactor.cli.main.run()
        sys.argv = saved_args

    @with_all_sources  # noqa
    def test_simple_privileged_build(self, is_registry_running, temp_image_name,
                                     source_provider, uri):
        if MOCK:
            mock_docker()

        temp_image = temp_image_name
        command = [
            "main.py",
            "--verbose",
            "build",
            source_provider,
            "--method", "privileged",
            "--build-image", PRIV_BUILD_IMAGE,
            "--image", temp_image.to_str(),
            "--uri", uri,
        ]
        if is_registry_running:
            logger.info("registry is running")
            command += ["--source-registry", LOCALHOST_REGISTRY]
        else:
            logger.info("registry is NOT running")
        with pytest.raises(SystemExit) as excinfo:
            self.exec_cli(command)

        assert excinfo.value.code == 0

    @with_all_sources  # noqa
    def test_simple_dh_build(self, is_registry_running, temp_image_name, source_provider, uri):
        if MOCK:
            mock_docker()

        temp_image = temp_image_name
        command = [
            "main.py",
            "--verbose",
            "build",
            source_provider,
            "--method", "hostdocker",
            "--build-image", DH_BUILD_IMAGE,
            "--image", temp_image.to_str(),
            "--uri", uri,
        ]
        if is_registry_running:
            logger.info("registry is running")
            command += ["--source-registry", LOCALHOST_REGISTRY]
        else:
            logger.info("registry is NOT running")
        with pytest.raises(SystemExit) as excinfo:
            self.exec_cli(command)
        assert excinfo.value.code == 0
        dt.remove_image(temp_image, noprune=True)

    def test_building_from_json_source_provider(self, is_registry_running, temp_image_name):  # noqa
        if MOCK:
            mock_docker()

        temp_image = temp_image_name
        command = [
            "main.py",
            "--verbose",
            "build",
            "json",
            "--method", "hostdocker",
            "--build-image", DH_BUILD_IMAGE,
            os.path.join(FILES, 'example-build.json'),
            "--substitute", "image={0}".format(temp_image),
            "source.uri={0}".format(DOCKERFILE_OK_PATH)
        ]
        if is_registry_running:
            logger.info("registry is running")
            command += ["--source-registry", LOCALHOST_REGISTRY]
        else:
            logger.info("registry is NOT running")
        with pytest.raises(SystemExit) as excinfo:
            self.exec_cli(command)
        assert excinfo.value.code == 0
        dt.remove_image(temp_image, noprune=True)

    def test_create_build_image(self, temp_image_name):  # noqa
        if MOCK:
            mock_docker()

        temp_image = temp_image_name
        priv_builder_path = os.path.join(reactor_root, 'images', 'privileged-builder')
        command = [
            "main.py",
            "--verbose",
            "create-build-image",
            "--reactor-local-path", reactor_root,
            priv_builder_path,
            temp_image.to_str(),
        ]
        with pytest.raises(SystemExit) as excinfo:
            self.exec_cli(command)
        assert excinfo.value.code == 0
        dt.remove_image(temp_image, noprune=True)

    def test_log_encoding(self, caplog, monkeypatch):
        if MOCK:
            mock_docker()

        (flexmock(InputPluginsRunner)
            .should_receive('__init__')
            .and_raise(RuntimeError))

        monkeypatch.setenv('LC_ALL', 'en_US.UTF-8')
        monkeypatch.setenv(BUILD_JSON_ENV, '{}')
        command = [
            "main.py",
            "--verbose",
            "inside-build",
        ]
        with caplog.at_level(logging.INFO):
            with pytest.raises(RuntimeError):
                self.exec_cli(command)

        # first message should be 'log encoding: <encoding>'
        match = caplog.records[0].message.split(':')
        if not match:
            raise RuntimeError

        encoding = codecs.getreader(match[1])
        assert encoding == encodings.utf_8.StreamReader
