"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


To have everything for a build in dist-git you need to fetch artefacts using 'fedpkg sources'.

This plugin should do it.
"""
from __future__ import absolute_import

import os
import subprocess

from atomic_reactor.plugin import PreBuildPlugin
from atomic_reactor.plugins.pre_reactor_config import get_sources_command
from atomic_reactor.constants import PLUGIN_DISTGIT_FETCH_KEY


class DistgitFetchArtefactsPlugin(PreBuildPlugin):
    key = PLUGIN_DISTGIT_FETCH_KEY
    is_allowed_to_fail = False

    def __init__(self, tasker, workflow, command=None):
        """
        constructor

        :param tasker: ContainerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param command: str, command to use to get artefacts (e.g. 'make sources')
                             it is executed in cloned git repo
        """
        # call parent constructor
        super(DistgitFetchArtefactsPlugin, self).__init__(tasker, workflow)
        self.command = get_sources_command(workflow, command)

    def run(self):
        """
        fetch artefacts
        """
        source_path = self.workflow.source.path
        cur_dir = os.getcwd()
        os.chdir(source_path)

        self.log.info("Calling: %s", self.command)
        try:
            subprocess.check_output(self.command.split(), stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            self.log.error("failed to fetch sources from git cache :\n%s", e.output)
            raise
        finally:
            os.chdir(cur_dir)
