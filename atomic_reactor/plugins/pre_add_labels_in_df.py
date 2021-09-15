"""
Copyright (c) 2015 Red Hat, Inc
All rights reserved.

This software may be modified and distributed under the terms
of the BSD license. See the LICENSE file for details.


Pre build plugin which adds labels to dockerfile. Labels have to be specified either
as a dict:

{
    "name": "add_labels_in_dockerfile",
    "args": {
        "labels": {
            "label1": "value1",
            "label 2": "some value"
        }
    }
}

Or as a string, which must be a dict serialised as JSON.

this will add turn this dockerfile:

```dockerfile
FROM fedora
CMD date
```

into this:

```dockerfile
FROM fedora
LABEL "label1"="value1" "label 2"="some value"
CMD date
```


By default there is parameter:
    dont_overwrite=("Architecture", "architecture")
which disallows to overwrite labels in the list if they are in parent image.

After that is also another check via parameter :
    dont_overwrite_if_in_dockerfile=("distribution-scope",)
which disallows to overwrite labels in the list if they are in dockerfile

Keys and values are quoted as necessary.


Equal labels, are more precisely labels of equal preferences, as they might
have same values, in case there is more equal labels specified in dockerfile
with different values, the value from the first in the list will be used
to set value for the missing ones.
"""

from atomic_reactor import start_time as atomic_reactor_start_time
from atomic_reactor.plugin import PreBuildPlugin
from atomic_reactor.constants import INSPECT_CONFIG
from atomic_reactor.util import (df_parser,
                                 get_docker_architecture,
                                 label_to_string,
                                 LabelFormatter)
from osbs.utils import Labels
import json
import datetime


class AddLabelsPlugin(PreBuildPlugin):
    key = "add_labels_in_dockerfile"
    is_allowed_to_fail = False

    def __init__(self, tasker, workflow, labels=None, dont_overwrite=None,
                 auto_labels=("build-date",
                              "architecture",
                              "vcs-type",
                              "vcs-ref",
                              "com.redhat.build-host"),
                 aliases=None,
                 dont_overwrite_if_in_dockerfile=("distribution-scope",
                                                  "com.redhat.license_terms")):
        """
        constructor

        :param tasker: ContainerTasker instance
        :param workflow: DockerBuildWorkflow instance
        :param labels: dict, key value pairs to set as labels; or str, JSON-encoded dict
        :param dont_overwrite: iterable, list of label keys which should not be overwritten
                               if they are present in parent image
        :param auto_labels: iterable, list of labels to be determined automatically, if supported
                            it should contain only new label names and not old label names,
                            as they will be managed automatically
        :param aliases: dict, maps old label names to new label names - for each old name found in
                        base image, dockerfile, or labels argument, a label with the new name is
                        added (with the same value)
        :param dont_overwrite_if_in_dockerfile : iterable, list of label keys which should not be
                                                 overwritten if they are present in dockerfile
        """
        # call parent constructor
        super(AddLabelsPlugin, self).__init__(tasker, workflow)

        if isinstance(labels, str):
            labels = json.loads(labels)
        if labels and not isinstance(labels, dict):
            raise RuntimeError("labels have to be dict")

        # see if REACTOR_CONFIG has any labels. If so, merge them with the existing argument
        # and otherwise use the existing argument
        image_labels = self.workflow.conf.image_labels

        # validity of image_labels is enforced by REACTOR_CONFIG's schema, so no need to check
        if image_labels:
            if labels:
                labels.update(image_labels)
            else:
                labels = image_labels

        self.labels = labels

        self.dont_overwrite = dont_overwrite or ()
        self.dont_overwrite_if_in_dockerfile = dont_overwrite_if_in_dockerfile
        self.aliases = aliases or Labels.get_new_names_by_old()
        self.auto_labels = auto_labels or ()
        self.info_url_format = self.workflow.conf.image_label_info_url_format

        self.equal_labels = self.workflow.conf.image_equal_labels
        if not isinstance(self.equal_labels, list):
            raise RuntimeError("equal_labels have to be list")

    def generate_auto_labels(self, base_labels, df_labels, plugin_labels):
        generated = {}
        all_labels = base_labels.copy()
        all_labels.update(df_labels)
        all_labels.update(plugin_labels)

        # build date
        dt = datetime.datetime.utcfromtimestamp(atomic_reactor_start_time)
        generated['build-date'] = dt.isoformat()

        # architecture - assuming host and image architecture is the same
        generated['architecture'], _ = get_docker_architecture(self.tasker)

        # build host
        docker_info = self.tasker.get_info()
        generated['com.redhat.build-host'] = docker_info['Name']

        # VCS info
        vcs = self.workflow.source.get_vcs_info()
        if vcs:
            generated['vcs-type'] = vcs.vcs_type
            generated['vcs-url'] = vcs.vcs_url
            generated['vcs-ref'] = vcs.vcs_ref

        for lbl in self.auto_labels:
            if lbl not in generated:
                self.log.warning("requested automatic label %r is not available", lbl)

            else:
                self.labels[lbl] = generated[lbl]
                self.log.info("automatic label %r is generated to %r", lbl, generated[lbl])

    def add_aliases(self, base_labels, df_labels, plugin_labels):
        all_labels = base_labels.copy()
        all_labels.update(df_labels)
        all_labels.update(plugin_labels)
        new_labels = df_labels.copy()
        new_labels.update(plugin_labels)

        applied_alias = False
        not_applied = []

        def add_as_an_alias(set_to, set_from):
            self.log.warning("adding label %r as an alias for label %r", set_to, set_from)
            self.labels[set_to] = all_labels[set_from]
            self.log.info(self.labels)
            return True

        for old, new in self.aliases.items():
            if old not in all_labels:
                applied_alias = not_applied.append(old)
                continue

            # new label doesn't exists but old label does
            # add new label with value from old label
            if new not in all_labels:
                applied_alias = add_as_an_alias(new, old)
                continue

            # new and old label exists, and have same value
            if all_labels[old] == all_labels[new]:
                self.log.debug("alias label %r for %r already exists, skipping", new, old)
                continue

            # new overwrites old, if new is explicitly specified,
            # or if old and new are in baseimage
            if new in new_labels or (new not in new_labels and old not in new_labels):
                applied_alias = add_as_an_alias(old, new)
                continue

            # old is explicitly specified so overwriting new (from baseimage)
            applied_alias = add_as_an_alias(new, old)
            # this will ensure that once we've added once new label based on
            # old label, if there are multiple old names, just first will be used
            all_labels[new] = all_labels[old]

        # warn if we applied only some aliases
        if applied_alias and not_applied:
            self.log.debug("applied only some aliases, following old labels were not found: %s",
                           ", ".join(not_applied))

        def set_missing_labels(labels_found, all_labels, value_from, not_in=(), not_value=None):
            labels_to_set = all_labels.difference(set(labels_found))

            for set_label in labels_to_set:
                if set_label in not_in and value_from[labels_found[0]] == not_value[set_label]:
                    self.log.debug("skipping label %r because it is set correctly in base image",
                                   set_label)
                else:
                    self.labels[set_label] = value_from[labels_found[0]]
                    self.log.warning("adding equal label %r with value %r",
                                     set_label, value_from[labels_found[0]])

        for equal_list in self.equal_labels:
            all_equal = set(equal_list)
            found_labels_base = []
            found_labels_new = []
            for equal_label in equal_list:
                if equal_label in new_labels:
                    found_labels_new.append(equal_label)
                elif equal_label in base_labels:
                    found_labels_base.append(equal_label)

            if found_labels_new:

                set_missing_labels(found_labels_new, all_equal, new_labels,
                                   found_labels_base, base_labels)

            elif found_labels_base:
                set_missing_labels(found_labels_base, all_equal, base_labels)

    def add_info_url(self, base_labels, df_labels, plugin_labels):
        all_labels = base_labels.copy()
        all_labels.update(df_labels)
        all_labels.update(plugin_labels)

        info_url = LabelFormatter().vformat(self.info_url_format, [], all_labels)
        self.labels['url'] = info_url

    def add_release_env_var(self, df_parser):
        release_env_var = self.workflow.source.config.release_env_var
        if release_env_var:
            final_labels = Labels(df_parser.labels)
            try:
                _, final_release = final_labels.get_name_and_value(Labels.LABEL_TYPE_RELEASE)
                release_line = "ENV {}={}".format(release_env_var, final_release)
                df_parser.add_lines(release_line, at_start=True, all_stages=True)
            except KeyError:
                self.log.warning("environment release variable %s could not be set because no "
                                 "release label found", release_env_var)

    def run(self):
        """
        run the plugin
        """
        dockerfile = df_parser(self.workflow.df_path, workflow=self.workflow)

        lines = dockerfile.lines

        if (self.workflow.dockerfile_images.custom_base_image or
                self.workflow.dockerfile_images.base_from_scratch):
            base_image_labels = {}
        else:
            try:
                config = self.workflow.builder.base_image_inspect[INSPECT_CONFIG]
            except KeyError as exc:
                message = "base image was not inspected"
                self.log.error(message)
                raise RuntimeError(message) from exc
            else:
                base_image_labels = config["Labels"] or {}

        self.generate_auto_labels(base_image_labels.copy(), dockerfile.labels.copy(),
                                  self.labels.copy())
        # changing dockerfile.labels writes out modified Dockerfile - err on
        # the safe side and make a copy
        self.add_aliases(base_image_labels.copy(), dockerfile.labels.copy(), self.labels.copy())
        if self.info_url_format:
            self.add_info_url(base_image_labels.copy(), dockerfile.labels.copy(),
                              self.labels.copy())

        labels = []
        for key, value in self.labels.items():

            if key not in dockerfile.labels or dockerfile.labels[key] != value:

                if key in self.dont_overwrite_if_in_dockerfile and key in dockerfile.labels:
                    self.log.info("denying overwrite of label %r, using from Dockerfile", key)

                elif (key in base_image_labels and
                      key in self.dont_overwrite and
                      key not in dockerfile.labels):
                    self.log.info("denying overwrite of label %r, using from baseimage", key)

                else:
                    label = label_to_string(key, value)
                    self.log.info("setting label %r", label)
                    labels.append(label)

        content = ""
        if labels:
            content = 'LABEL ' + " ".join(labels)
            # put labels at the end of dockerfile (since they change metadata and do not interact
            # with FS, this should cause no harm)
            lines.append('\n' + content + '\n')
            dockerfile.lines = lines

        self.add_release_env_var(dockerfile)

        return content
