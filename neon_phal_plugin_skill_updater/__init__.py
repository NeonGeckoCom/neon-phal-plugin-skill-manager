# NEON AI (TM) SOFTWARE, Software Development Kit & Application Framework
# All trademark and other rights reserved by their respective owners
# Copyright 2008-2022 Neongecko.com Inc.
# Contributors: Daniel McKnight, Guy Daniels, Elon Gasper, Richard Leeds,
# Regina Bloomstine, Casimiro Ferreira, Andrii Pernatii, Kirill Hrymailo
# BSD-3 License
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from this
#    software without specific prior written permission.
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR
# CONTRIBUTORS  BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA,
# OR PROFITS;  OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE,  EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import pkg_resources

from dataclasses import dataclass, asdict
from typing import List
from os import listdir
from os.path import isdir, join

from mycroft_bus_client import Message
from ovos_utils.log import LOG
from ovos_plugin_manager.phal import PHALPlugin
from ovos_skills_manager.utils import get_skill_directories
from neon_phal_plugin_skill_updater.skill_utils import get_remote_entries, get_pypi_package_versions


@dataclass
class InstalledSkill:
    skill_id: str = "unknown"
    pip_installed: bool = False
    pypi_name: str = None
    installed_version: str = "unknown"
    installed_path: str = None
    latest_version: str = "unknown"


class SkillUpdater(PHALPlugin):
    def __init__(self, bus=None, name="neon-phal-plugin-skill-updater",
                 config=None):
        PHALPlugin.__init__(self, bus, name, config)
        self.blacklist = self.config.get('blacklist') or list()
        self.bus.on("neon.skill_updater.check_updates", self.check_for_updates)
        self.bus.on("neon.skill_updater.update_skills", self.do_skill_updates)

    @property
    def config_default_skills(self) -> list:
        """
        Get a list of default skills from core configuration
        """
        default = self.config_core.get('skills', {}).get("default_skills") or []
        if isinstance(default, str):
            default = get_remote_entries(default)
        return default

    @property
    def config_essential_skills(self) -> list:
        """
        Get a list of essential skills from core configuration
        """
        essential = self.config_core.get('skills',
                                         {}).get("essential_skills") or []
        if isinstance(essential, str):
            essential = get_remote_entries(essential)
        return essential

    @property
    def pip_installed_skills(self) -> List[InstalledSkill]:
        """
        Get a list of pip-installed skills name, version
        """
        return [InstalledSkill(ep.name, True, ep.dist.project_name,
                               ep.dist.version, join(ep.dist.module_path,
                                                     ep.module_name))
                for ep in pkg_resources.iter_entry_points('ovos.plugin.skill')
                if ep.name not in self.blacklist]

    @property
    def git_installed_skills(self) -> List[InstalledSkill]:
        """
        Get a list of git-installed skill directories
        """
        skills = list()
        for skills_dir in get_skill_directories(self.config_core):
            if not isdir(skills_dir):
                LOG.warning(f"No such directory: {skills_dir}")
                continue
            for skill in listdir(skills_dir):
                if skill not in self.blacklist and \
                        isdir(join(skills_dir, skill)):
                    skills.append(InstalledSkill(skill,
                                                 installed_path=join(skills_dir,
                                                                     skill)))
        return skills

    def check_for_updates(self, message: Message):
        """
        Handle a request to check for skill updates
        """
        pip = self.check_pip_updates()
        git = self.check_git_updates()
        self.bus.emit(message.response({'pip': [asdict(s) for s in pip],
                                        'git': [asdict(s) for s in git]}))

    def do_skill_updates(self, message: Message = None):
        """
        Handle a request to update skills and emit a response
        """
        if not message:
            update_pip = True
            update_git = True
        else:
            update_pip = message.data.get('do_pip') or False
            update_git = message.data.get('do_git') or False

        if update_pip:
            pip_skills = self.check_pip_updates()
            pip_status = all((self._update_skill_pip(skill) for
                              skill in pip_skills))
        else:
            pip_status = None
        if update_git:
            git_skills = self.check_git_updates()
            git_status = all((self._update_skill_git(skill) for
                              skill in git_skills))
        else:
            git_status = None

        if message:
            self.bus.emit(message.response({"pip_status": pip_status,
                                            "git_status": git_status}))

    def check_pip_updates(self) -> List[InstalledSkill]:
        """
        Return a list of pip-installed skills that have available updates.
        """
        updatable_skills = list()
        for skill in self.pip_installed_skills:
            try:
                if self._check_pip_skill_update(skill):
                    updatable_skills.append(skill)
            except Exception as e:
                LOG.exception(e)
        return updatable_skills

    @staticmethod
    def _check_pip_skill_update(skill: InstalledSkill) -> True:
        """
        Check if the passed skill is updatable
        :param skill: InstalledSkill to check
        """
        if not skill.pip_installed:
            raise RuntimeError(f"Expected pip-installed skill, got: {skill}")
        installed_ver = skill.installed_version
        pypi_versions = get_pypi_package_versions(skill.pypi_name)
        if 'a' in installed_ver:
            latest_ver = pypi_versions[-1]
        else:
            LOG.debug("Ignoring alpha versions")
            pypi_versions.reverse()
            latest_ver = pypi_versions[-1]  # Enforce some default
            for ver in pypi_versions:
                if 'a' not in ver:
                    latest_ver = ver
                    break
        skill.latest_version = latest_ver
        if installed_ver != latest_ver:
            LOG.debug(f"{skill.skill_id} {installed_ver} -> "
                      f"{latest_ver}")
            return True
        return False

    def check_git_updates(self) -> List[InstalledSkill]:
        """
        Return a list of git-installed kills that have available updates
        """
        # TODO: Implement this check
        return self.git_installed_skills

    def update_skill(self, skill: InstalledSkill):
        """
        Update an installed skill to the latest version
        :param skill: Installed skill to update
        """
        if skill.pip_installed:
            return self._update_skill_pip(skill)
        else:
            return self._update_skill_git(skill)

    @staticmethod
    def _update_skill_git(skill: InstalledSkill) -> bool:
        """
        Update a git-installed skill to the latest available version
        :param skill: Installed skill to update
        :returns
        """
        from ovos_skills_manager.skill_entry import SkillEntry
        skill = SkillEntry.from_directory(skill.installed_path)
        return skill.update()

    @staticmethod
    def _update_skill_pip(skill: InstalledSkill) -> bool:
        """
        Update a pip-installed skill to the latest available version
        """
        if skill.latest_version == skill.installed_version:
            LOG.debug(f"skill already updated: {skill.skill_id}")
            return False

        # TODO: Update skill
