"""
Abstractions and utilities for working with projects to run ecosystem checks on.
"""

from __future__ import annotations

from asyncio import create_subprocess_exec
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from subprocess import PIPE
from typing import Self

from ruff_ecosystem import logger
from ruff_ecosystem.check import CheckOptions
from ruff_ecosystem.format import FormatOptions
from ruff_ecosystem.types import Serializable


@dataclass(frozen=True)
class Project(Serializable):
    """
    An ecosystem target
    """

    repo: Repository
    check_options: CheckOptions = field(default_factory=lambda: CheckOptions())
    format_options: FormatOptions = field(default_factory=lambda: FormatOptions())


class RuffCommand(Enum):
    check = "check"
    format = "format"


class ProjectSetupError(Exception):
    """An error setting up a project."""


@dataclass(frozen=True)
class Repository(Serializable):
    """
    A remote GitHub repository.
    """

    owner: str
    name: str
    ref: str | None

    @property
    def fullname(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def url(self: Self) -> str:
        return f"https://github.com/{self.owner}/{self.name}"

    async def clone(self: Self, checkout_dir: Path) -> ClonedRepository:
        """
        Shallow clone this repository
        """
        if checkout_dir.exists():
            logger.debug(f"Reusing {self.owner}:{self.name}")

            if self.ref:
                logger.debug(f"Checking out ref {self.ref}")
                process = await create_subprocess_exec(
                    *["git", "checkout", "-f", self.ref],
                    cwd=checkout_dir,
                    env={"GIT_TERMINAL_PROMPT": "0"},
                    stdout=PIPE,
                    stderr=PIPE,
                )
                if await process.wait() != 0:
                    _, stderr = await process.communicate()
                    raise ProjectSetupError(
                        f"Failed to checkout {self.ref}: {stderr.decode()}"
                    )

            return await ClonedRepository.from_path(checkout_dir, self)

        logger.debug(f"Cloning {self.owner}:{self.name} to {checkout_dir}")
        command = [
            "git",
            "clone",
            "--config",
            "advice.detachedHead=false",
            "--quiet",
            "--depth",
            "1",
            "--no-tags",
        ]
        if self.ref:
            command.extend(["--branch", self.ref])

        command.extend(
            [
                f"https://github.com/{self.owner}/{self.name}",
                str(checkout_dir),
            ],
        )

        process = await create_subprocess_exec(
            *command, env={"GIT_TERMINAL_PROMPT": "0"}
        )

        status_code = await process.wait()

        logger.debug(
            f"Finished cloning {self.fullname} with status {status_code}",
        )
        return await ClonedRepository.from_path(checkout_dir, self)


@dataclass(frozen=True)
class ClonedRepository(Repository, Serializable):
    """
    A cloned GitHub repository, which includes the hash of the current commit.
    """

    commit_hash: str
    path: Path

    def url_for(
        self: Self,
        path: str,
        line_number: int | None = None,
        end_line_number: int | None = None,
    ) -> str:
        """
        Return the remote GitHub URL for the given path in this repository.
        """
        url = f"https://github.com/{self.owner}/{self.name}/blob/{self.commit_hash}/{path}"
        if line_number:
            url += f"#L{line_number}"
        if end_line_number:
            url += f"-L{end_line_number}"
        return url

    @property
    def url(self: Self) -> str:
        return f"https://github.com/{self.owner}/{self.name}@{self.commit_hash}"

    @classmethod
    async def from_path(cls, path: Path, repo: Repository):
        return cls(
            name=repo.name,
            owner=repo.owner,
            ref=repo.ref,
            path=path,
            commit_hash=await cls._get_head_commit(path),
        )

    @staticmethod
    async def _get_head_commit(checkout_dir: Path) -> str:
        """
        Return the commit sha for the repository in the checkout directory.
        """
        process = await create_subprocess_exec(
            *["git", "rev-parse", "HEAD"],
            cwd=checkout_dir,
            stdout=PIPE,
        )
        stdout, _ = await process.communicate()
        if await process.wait() != 0:
            raise ProjectSetupError(f"Failed to retrieve commit sha at {checkout_dir}")

        return stdout.decode().strip()
