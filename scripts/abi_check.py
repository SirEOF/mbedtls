#!/usr/bin/env python3
"""
This file is part of Mbed TLS (https://tls.mbed.org)

Copyright (c) 2018, Arm Limited, All Rights Reserved

Purpose

This script is a small wrapper around the abi-compliance-checker and
abi-dumper tools, applying them to compare the ABI and API of the library
files from two different Git revisions within an Mbed TLS repository.
The results of the comparison are formatted as HTML and stored at
a configurable location. Returns 0 on success, 1 on ABI/API non-compliance,
and 2 if there is an error while running the script.
Note: must be run from Mbed TLS root.
"""

import os
import sys
import traceback
import shutil
import subprocess
import argparse
import logging
import tempfile


class AbiChecker(object):
    """API and ABI checker."""

    def __init__(self, report_dir, old_repo, old_rev, new_repo, new_rev,
                 keep_all_reports, skip_file=None):
        """Instantiate the API/ABI checker.

        report_dir: directory for output files
        old_repo: repository for git revision to compare against
        old_rev: reference git revision to compare against
        new_repo: repository for git revision to check
        new_rev: git revision to check
        keep_all_reports: if false, delete old reports
        skip_file: path to file containing symbols and types to skip
        """
        self.repo_path = "."
        self.log = None
        self.setup_logger()
        self.report_dir = os.path.abspath(report_dir)
        self.keep_all_reports = keep_all_reports
        self.should_keep_report_dir = os.path.isdir(self.report_dir)
        self.old_repo = old_repo
        self.old_rev = old_rev
        self.new_repo = new_repo
        self.new_rev = new_rev
        self.skip_file = skip_file
        self.mbedtls_modules = ["libmbedcrypto", "libmbedtls", "libmbedx509"]
        self.old_dumps = {}
        self.new_dumps = {}
        self.git_command = "git"
        self.make_command = "make"

    @staticmethod
    def check_repo_path():
        current_dir = os.path.realpath('.')
        root_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        if current_dir != root_dir:
            raise Exception("Must be run from Mbed TLS root")

    def setup_logger(self):
        self.log = logging.getLogger()
        self.log.setLevel(logging.INFO)
        self.log.addHandler(logging.StreamHandler())

    @staticmethod
    def check_abi_tools_are_installed():
        for command in ["abi-dumper", "abi-compliance-checker"]:
            if not shutil.which(command):
                raise Exception("{} not installed, aborting".format(command))

    def get_clean_worktree_for_git_revision(self, remote_repo, git_rev):
        """Make a separate worktree with git_rev checked out.
        Do not modify the current worktree."""
        git_worktree_path = tempfile.mkdtemp()
        if remote_repo:
            self.log.info(
                "Checking out git worktree for revision {} from {}".format(
                    git_rev, remote_repo
                )
            )
            fetch_process = subprocess.Popen(
                [self.git_command, "fetch", remote_repo, git_rev],
                cwd=self.repo_path,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )
            fetch_output, _ = fetch_process.communicate()
            self.log.info(fetch_output.decode("utf-8"))
            if fetch_process.returncode != 0:
                raise Exception("Fetching revision failed, aborting")
            worktree_rev = "FETCH_HEAD"
        else:
            self.log.info(
                "Checking out git worktree for revision {}".format(git_rev)
            )
            worktree_rev = git_rev
        worktree_process = subprocess.Popen(
            [self.git_command, "worktree", "add", "--detach",
             git_worktree_path, worktree_rev],
            cwd=self.repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        worktree_output, _ = worktree_process.communicate()
        self.log.info(worktree_output.decode("utf-8"))
        if worktree_process.returncode != 0:
            raise Exception("Checking out worktree failed, aborting")
        return git_worktree_path

    def update_git_submodules(self, git_worktree_path):
        process = subprocess.Popen(
            [self.git_command, "submodule", "update", "--init", '--recursive'],
            cwd=git_worktree_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        output, _ = process.communicate()
        self.log.info(output.decode("utf-8"))
        if process.returncode != 0:
            raise Exception("git submodule update failed, aborting")

    def build_shared_libraries(self, git_worktree_path):
        """Build the shared libraries in the specified worktree."""
        my_environment = os.environ.copy()
        my_environment["CFLAGS"] = "-g -Og"
        my_environment["SHARED"] = "1"
        make_process = subprocess.Popen(
            self.make_command,
            env=my_environment,
            cwd=git_worktree_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        make_output, _ = make_process.communicate()
        self.log.info(make_output.decode("utf-8"))
        if make_process.returncode != 0:
            raise Exception("make failed, aborting")

    def get_abi_dumps_from_shared_libraries(self, git_ref, git_worktree_path):
        """Generate the ABI dumps for the specified git revision.
        It must be checked out in git_worktree_path and the shared libraries
        must have been built."""
        abi_dumps = {}
        for mbed_module in self.mbedtls_modules:
            output_path = os.path.join(
                self.report_dir, "{}-{}.dump".format(mbed_module, git_ref)
            )
            abi_dump_command = [
                "abi-dumper",
                os.path.join(
                    git_worktree_path, "library", mbed_module + ".so"),
                "-o", output_path,
                "-lver", git_ref
            ]
            abi_dump_process = subprocess.Popen(
                abi_dump_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )
            abi_dump_output, _ = abi_dump_process.communicate()
            self.log.info(abi_dump_output.decode("utf-8"))
            if abi_dump_process.returncode != 0:
                raise Exception("abi-dumper failed, aborting")
            abi_dumps[mbed_module] = output_path
        return abi_dumps

    def cleanup_worktree(self, git_worktree_path):
        """Remove the specified git worktree."""
        shutil.rmtree(git_worktree_path)
        worktree_process = subprocess.Popen(
            [self.git_command, "worktree", "prune"],
            cwd=self.repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        worktree_output, _ = worktree_process.communicate()
        self.log.info(worktree_output.decode("utf-8"))
        if worktree_process.returncode != 0:
            raise Exception("Worktree cleanup failed, aborting")

    def get_abi_dump_for_ref(self, remote_repo, git_rev):
        """Generate the ABI dumps for the specified git revision."""
        git_worktree_path = self.get_clean_worktree_for_git_revision(
            remote_repo, git_rev
        )
        self.update_git_submodules(git_worktree_path)
        self.build_shared_libraries(git_worktree_path)
        abi_dumps = self.get_abi_dumps_from_shared_libraries(
            git_rev, git_worktree_path
        )
        self.cleanup_worktree(git_worktree_path)
        return abi_dumps

    def get_abi_compatibility_report(self):
        """Generate a report of the differences between the reference ABI
        and the new ABI. ABI dumps from self.old_rev and self.new_rev must
        be available."""
        compatibility_report = ""
        compliance_return_code = 0
        for mbed_module in self.mbedtls_modules:
            output_path = os.path.join(
                self.report_dir, "{}-{}-{}.html".format(
                    mbed_module, self.old_rev, self.new_rev
                )
            )
            abi_compliance_command = [
                "abi-compliance-checker",
                "-l", mbed_module,
                "-old", self.old_dumps[mbed_module],
                "-new", self.new_dumps[mbed_module],
                "-strict",
                "-report-path", output_path
            ]
            if self.skip_file:
                abi_compliance_command += ["-skip-symbols", self.skip_file,
                                           "-skip-types", self.skip_file]
            abi_compliance_process = subprocess.Popen(
                abi_compliance_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT
            )
            abi_compliance_output, _ = abi_compliance_process.communicate()
            self.log.info(abi_compliance_output.decode("utf-8"))
            if abi_compliance_process.returncode == 0:
                compatibility_report += (
                    "No compatibility issues for {}\n".format(mbed_module)
                )
                if not self.keep_all_reports:
                    os.remove(output_path)
            elif abi_compliance_process.returncode == 1:
                compliance_return_code = 1
                self.should_keep_report_dir = True
                compatibility_report += (
                    "Compatibility issues found for {}, "
                    "for details see {}\n".format(mbed_module, output_path)
                )
            else:
                raise Exception(
                    "abi-compliance-checker failed with a return code of {},"
                    " aborting".format(abi_compliance_process.returncode)
                )
            os.remove(self.old_dumps[mbed_module])
            os.remove(self.new_dumps[mbed_module])
        if not self.should_keep_report_dir and not self.keep_all_reports:
            os.rmdir(self.report_dir)
        self.log.info(compatibility_report)
        return compliance_return_code

    def check_for_abi_changes(self):
        """Generate a report of ABI differences
        between self.old_rev and self.new_rev."""
        self.check_repo_path()
        self.check_abi_tools_are_installed()
        self.old_dumps = self.get_abi_dump_for_ref(self.old_repo, self.old_rev)
        self.new_dumps = self.get_abi_dump_for_ref(self.new_repo, self.new_rev)
        return self.get_abi_compatibility_report()


def run_main():
    try:
        parser = argparse.ArgumentParser(
            description=(
                """This script is a small wrapper around the
                abi-compliance-checker and abi-dumper tools, applying them
                to compare the ABI and API of the library files from two
                different Git revisions within an Mbed TLS repository.
                The results of the comparison are formatted as HTML and stored
                at a configurable location. Returns 0 on success, 1 on ABI/API
                non-compliance, and 2 if there is an error while running the
                script. Note: must be run from Mbed TLS root."""
            )
        )
        parser.add_argument(
            "-r", "--report-dir", type=str, default="reports",
            help="directory where reports are stored, default is reports",
        )
        parser.add_argument(
            "-k", "--keep-all-reports", action="store_true",
            help="keep all reports, even if there are no compatibility issues",
        )
        parser.add_argument(
            "-o", "--old-rev", type=str,
            help=("revision for old version."
                  "Can include repository before revision"),
            required=True, nargs="+"
        )
        parser.add_argument(
            "-n", "--new-rev", type=str,
            help=("revision for new version"
                  "Can include repository before revision"),
            required=True, nargs="+"
        )
        parser.add_argument(
            "-s", "--skip-file", type=str,
            help="path to file containing symbols and types to skip"
        )
        abi_args = parser.parse_args()
        if len(abi_args.old_rev) == 1:
            old_repo = None
            old_rev = abi_args.old_rev[0]
        elif len(abi_args.old_rev) == 2:
            old_repo = abi_args.old_rev[0]
            old_rev = abi_args.old_rev[1]
        else:
            raise Exception("Too many arguments passed for old version")
        if len(abi_args.new_rev) == 1:
            new_repo = None
            new_rev = abi_args.new_rev[0]
        elif len(abi_args.new_rev) == 2:
            new_repo = abi_args.new_rev[0]
            new_rev = abi_args.new_rev[1]
        else:
            raise Exception("Too many arguments passed for new version")
        abi_check = AbiChecker(
            abi_args.report_dir, old_repo, old_rev,
            new_repo, new_rev, abi_args.keep_all_reports,
            abi_args.skip_file
        )
        return_code = abi_check.check_for_abi_changes()
        sys.exit(return_code)
    except Exception:
        traceback.print_exc()
        sys.exit(2)


if __name__ == "__main__":
    run_main()
