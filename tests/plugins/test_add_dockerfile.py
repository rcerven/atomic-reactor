"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.
"""

from flexmock import flexmock
from atomic_reactor.plugin import PreBuildPluginsRunner
from atomic_reactor.plugins.pre_add_dockerfile import AddDockerfilePlugin
from atomic_reactor.plugins.pre_add_labels_in_df import AddLabelsPlugin
from atomic_reactor.util import df_parser
from atomic_reactor.constants import INSPECT_CONFIG
from tests.constants import MOCK
from tests.stubs import StubInsideBuilder, StubSource
if MOCK:
    from tests.docker_mock import mock_docker


def prepare(workflow, df_path):
    workflow.source = StubSource()
    workflow.builder = StubInsideBuilder().for_workflow(workflow)
    flexmock(workflow, df_path=df_path)



def test_adddockerfile_plugin(tmpdir, docker_tasker, workflow):  # noqa
    df_content = """
FROM fedora
RUN yum install -y python-django
CMD blabla"""
    df = df_parser(str(tmpdir))
    df.content = df_content

    prepare(workflow, df.dockerfile_path)

    runner = PreBuildPluginsRunner(
        docker_tasker,
        workflow,
        [{
            'name': AddDockerfilePlugin.key,
            'args': {'nvr': 'rhel-server-docker-7.1-20'}
        }]
    )
    runner.run()
    assert AddDockerfilePlugin.key is not None

    expected_output = """
FROM fedora
RUN yum install -y python-django
ADD Dockerfile-rhel-server-docker-7.1-20 /root/buildinfo/Dockerfile-rhel-server-docker-7.1-20
CMD blabla"""
    assert df.content == expected_output


def test_adddockerfile_todest(tmpdir, docker_tasker, workflow):  # noqa
    df_content = """
FROM fedora
RUN yum install -y python-django
CMD blabla"""
    df = df_parser(str(tmpdir))
    df.content = df_content

    prepare(workflow, df.dockerfile_path)

    runner = PreBuildPluginsRunner(
        docker_tasker,
        workflow,
        [{
            'name': AddDockerfilePlugin.key,
            'args': {'nvr': 'jboss-eap-6-docker-6.4-77',
                     'destdir': '/usr/share/doc/'}
        }]
    )
    runner.run()
    assert AddDockerfilePlugin.key is not None

    expected_output = """
FROM fedora
RUN yum install -y python-django
ADD Dockerfile-jboss-eap-6-docker-6.4-77 /usr/share/doc/Dockerfile-jboss-eap-6-docker-6.4-77
CMD blabla"""
    assert df.content == expected_output


def test_adddockerfile_nvr_from_labels(tmpdir, docker_tasker, workflow):  # noqa
    df_content = """
FROM fedora
RUN yum install -y python-django
LABEL Name="jboss-eap-6-docker" "Version"="6.4" "Release"=77
CMD blabla"""
    df = df_parser(str(tmpdir))
    df.content = df_content

    prepare(workflow, df.dockerfile_path)

    runner = PreBuildPluginsRunner(
        docker_tasker,
        workflow,
        [{
            'name': AddDockerfilePlugin.key
        }]
    )
    runner.run()
    assert AddDockerfilePlugin.key is not None

    assert "ADD Dockerfile-jboss-eap-6-docker-6.4-77 /root/buildinfo/Dockerfile-jboss-eap-6-docker-6.4-77" in df.content  # noqa


def test_adddockerfile_nvr_from_labels2(tmpdir, docker_tasker, workflow):  # noqa
    df_content = """
FROM fedora
RUN yum install -y python-django
CMD blabla"""
    df = df_parser(str(tmpdir))
    df.content = df_content

    if MOCK:
        mock_docker()

    prepare(workflow, df.dockerfile_path)
    workflow.builder.set_inspection_data({INSPECT_CONFIG: {"Labels": {}}})
    workflow.builder.set_dockerfile_images(df.parent_images)

    runner = PreBuildPluginsRunner(
        docker_tasker,
        workflow,
        [{
            'name': AddLabelsPlugin.key,
            'args': {'labels': {'Name': 'jboss-eap-6-docker',
                                'Version': '6.4',
                                'Release': '77'},
                     'auto_labels': []}
         },
         {
            'name': AddDockerfilePlugin.key
        }]
    )
    runner.run()
    assert AddDockerfilePlugin.key is not None

    assert "ADD Dockerfile-jboss-eap-6-docker-6.4-77 /root/buildinfo/Dockerfile-jboss-eap-6-docker-6.4-77" in df.content  # noqa


def test_adddockerfile_fails(tmpdir, docker_tasker, caplog, workflow):  # noqa
    df_content = """
FROM fedora
RUN yum install -y python-django
CMD blabla"""
    df = df_parser(str(tmpdir))
    df.content = df_content

    prepare(workflow, df.dockerfile_path)

    runner = PreBuildPluginsRunner(
        docker_tasker,
        workflow,
        [{
            'name': AddDockerfilePlugin.key
        }]
    )
    runner.run()
    assert "plugin 'add_dockerfile' raised an exception: ValueError" in caplog.text


def test_adddockerfile_final(tmpdir, docker_tasker, workflow):  # noqa
    df_content = """
FROM fedora
RUN yum install -y python-django
CMD blabla"""
    df = df_parser(str(tmpdir))
    df.content = df_content

    prepare(workflow, df.dockerfile_path)

    runner = PreBuildPluginsRunner(
        docker_tasker,
        workflow,
        [{
             'name': AddDockerfilePlugin.key,
             'args': {'nvr': 'rhel-server-docker-7.1-20', "use_final_dockerfile": True}
        }]
    )
    runner.run()
    assert AddDockerfilePlugin.key is not None

    expected_output = """
FROM fedora
RUN yum install -y python-django
ADD Dockerfile /root/buildinfo/Dockerfile-rhel-server-docker-7.1-20
CMD blabla"""
    assert df.content == expected_output
