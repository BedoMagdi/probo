#!/usr/bin/python3
"""Appends JVM arguments to every argLine in the pom.xml files of a project, or restores the files.

-DargLine on the Maven command line has no effect on a pom.xml that sets argLine itself, so for
such a project the JVM arguments have to be added to the file.
"""

import os
import argparse
import subprocess


def insert_in_all_poms(args, target_dir):
    """Append args to every argLine in the pom.xml files under target_dir."""
    for dir_path, _, files in os.walk(target_dir):
        if 'pom.xml' not in files:
            continue
        pom = os.path.join(dir_path, 'pom.xml')
        with open(pom) as f:
            content = f.read()
        if '</argLine>' in content:
            with open(pom, 'w') as f:
                f.write(content.replace('</argLine>', ' ' + args + '</argLine>'))


def restore_all_poms(target_dir):
    """Discard all changes to the tracked files of the project in target_dir."""
    subprocess.run(["git", "checkout", "--", "."], cwd=target_dir)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Append JVM arguments to every argLine in the pom.xml files of a project.")
    parser.add_argument('--dir', required=True, help="project directory; the pom.xml files in it and below it are changed")
    parser.add_argument('--args', default='', help="JVM arguments to append")
    parser.add_argument('--restore', action='store_true', help="restore the project files to their committed state instead")
    opts = parser.parse_args()

    if opts.restore:
        restore_all_poms(opts.dir)
    elif opts.args.strip():
        insert_in_all_poms(opts.args, opts.dir)
