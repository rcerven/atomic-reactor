"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


Include user-provided Dockerfile in the IMAGE_BUILD_INFO_DIR
(or other if provided) directory in the built image.
This is accomplished by appending an ADD command to it.
Name of the Dockerfile is changed to include N-V-R of the build.
N-V-R is specified either by nvr argument OR from
Name/Version/Release labels in Dockerfile.
If you run add_labels_in_dockerfile to add Name/Version/Release labels
you have to run it BEFORE this one.


Example configuration:
{
    'name': 'add_dockerfile',
    'args': {'nvr': 'rhel-server-docker-7.1-20'}
}

or

[{
   'name': 'add_labels_in_dockerfile',
   'args': {'labels': {'Name': 'jboss-eap-6-docker',
                       'Version': '6.4',
                       'Release': '77'}}
},
{
   'name': 'add_dockerfile'
}]

"""

import os
import shutil
from atomic_reactor.constants import DOCKERFILE_FILENAME, IMAGE_BUILD_INFO_DIR
from atomic_reactor.plugin import PreBuildPlugin
from atomic_reactor.util import df_parser
from osbs.utils import Labels


class AddDockerfilePlugin(PreBuildPlugin):
    key = "add_dockerfile"

    def __init__(self, tasker, workflow, nvr=None, destdir=IMAGE_BUILD_INFO_DIR,
                 use_final_dockerfile=False):
        """
        constructor

        :param tasker: ContainerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param nvr: name-version-release, will be appended to Dockerfile-.
                    If not specified, try to get it from Name, Version, Release labels.
        :param destdir: directory in the image to put Dockerfile-N-V-R into
        :param use_final_dockerfile: bool, when set to True, uses final version of processed
                                     dockerfile,
                                     when set to False, uses Dockerfile from time when this plugin
                                     was executed
        """
        # call parent constructor
        super(AddDockerfilePlugin, self).__init__(tasker, workflow)

        self.use_final_dockerfile = use_final_dockerfile

        if nvr is None:
            labels = Labels(df_parser(self.workflow.df_path).labels)
            try:
                _, name = labels.get_name_and_value(Labels.LABEL_TYPE_NAME)
                _, version = labels.get_name_and_value(Labels.LABEL_TYPE_VERSION)
                _, release = labels.get_name_and_value(Labels.LABEL_TYPE_RELEASE)
            except KeyError as exc:
                raise ValueError(
                    "You have to specify either nvr arg or name/version/release labels."
                ) from exc
            nvr = "{0}-{1}-{2}".format(name, version, release)
            nvr = nvr.replace("/", "-")
        self.df_name = '{0}-{1}'.format(DOCKERFILE_FILENAME, nvr)
        self.df_dir = destdir
        self.df_path = os.path.join(self.df_dir, self.df_name)

        # we are not using final dockerfile, so let's copy current snapshot
        if not self.use_final_dockerfile:
            local_df_path = os.path.join(self.workflow.df_dir, self.df_name)
            shutil.copy2(self.workflow.df_path, local_df_path)

    def run(self):
        """
        run the plugin
        """
        dockerfile = df_parser(self.workflow.df_path, workflow=self.workflow)
        lines = dockerfile.lines

        # when using final dockerfile, we should use DOCKERFILE_FILENAME
        # otherwise we should use the copied version
        if self.use_final_dockerfile:
            content = 'ADD {0} {1}'.format(DOCKERFILE_FILENAME, self.df_path)
        else:
            content = 'ADD {0} {1}'.format(self.df_name, self.df_path)

        # put it before last instruction
        lines.insert(-1, content + '\n')

        dockerfile.lines = lines

        self.log.info("added %s", self.df_path)

        return content
