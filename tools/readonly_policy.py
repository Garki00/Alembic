# tools/readonly_policy.py
"""Read-only command policy: decide whether a whole Bash command is provably harmless.

The policy is a command/flag allowlist evaluated over the argv produced by
:mod:`tools.bash_analysis`. It answers ``True`` only when every invocation is
statically known to be read-only; unknown commands, unknown flags, write options,
output redirection, dynamic words, and unsupported syntax all fail closed.

Usage::

    if is_read_only_command(command, shell="gitbash"):
        ...  # no approval needed for this call

The classifier is a permission hint, not an isolation boundary: it must never be
used to grant filesystem or network access, only to skip a confirmation prompt.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Callable

from tools.bash_analysis import Analysis, Invocation, analyze

NONE = "none"
STRING = "string"
NUMBER = "number"
CHAR = "char"
BRACE = "{}"
EOF = "EOF"
OPTIONAL_STRING = "optionalString"

#: Shells where the analyzer can reason about POSIX shell syntax.
READ_ONLY_SHELLS = frozenset({"gitbash", "wsl", "bash", "sh", "zsh"})

SAFE_ENV_ASSIGNMENTS = frozenset(
    {
        "ANTHROPIC_API_KEY", "BLOCK_SIZE", "BLOCKSIZE", "CGO_ENABLED", "CHARSET", "CI",
        "CLICOLOR", "CLICOLOR_FORCE", "COLORTERM", "COLUMNS", "DEBIAN_FRONTEND",
        "FORCE_COLOR", "GCC_COLORS", "GIT_TERMINAL_PROMPT", "GO111MODULE", "GOARCH",
        "GOEXPERIMENT", "GOOS", "GREP_COLOR", "GREP_COLORS", "LANG", "LANGUAGE",
        "LC_ALL", "LC_CTYPE", "LC_TIME", "LINES", "LSCOLORS", "LS_COLORS", "NO_COLOR",
        "NODE_ENV", "PYTEST_DEBUG", "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
        "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "RUST_BACKTRACE", "RUST_LOG",
        "TERM", "TIME_STYLE", "TZ",
    }
)

SAFE_INPUT_REDIRECTS = frozenset({"<", "<&"})
OPTION_PATTERN = re.compile(r"^-[a-zA-Z0-9_-]")


# ---------------------------------------------------------------------------
# Flag tables
# ---------------------------------------------------------------------------
TREE_SAFE_FLAGS = {
    "-A": NONE, "-C": NONE, "-D": NONE, "-F": NONE, "-H": STRING, "-I": STRING,
    "-J": NONE, "-L": NUMBER, "-N": NONE, "-P": STRING, "-Q": NONE, "-S": NONE,
    "-T": STRING, "-U": NONE, "-X": NONE, "-a": NONE, "-c": NONE, "-d": NONE,
    "-f": NONE, "-g": NONE, "-h": NONE, "-i": NONE, "-l": NONE, "-n": NONE,
    "-p": NONE, "-q": NONE, "-r": NONE, "-s": NONE, "-t": NONE, "-u": NONE,
    "-v": NONE, "-x": NONE,
    "--authority": STRING, "--charset": STRING, "--device": NONE, "--dirsfirst": NONE,
    "--du": NONE, "--fflinks": NONE, "--filelimit": NUMBER, "--filesfirst": NONE,
    "--fromfile": NONE, "--fromtabfile": NONE, "--gitfile": STRING, "--gitignore": NONE,
    "--help": NONE, "--hintro": STRING, "--houtro": STRING, "--hyperlink": NONE,
    "--ignore-case": NONE, "--inodes": NONE, "--info": NONE, "--infofile": STRING,
    "--matchdirs": NONE, "--metafirst": NONE, "--nolinks": NONE, "--noreport": NONE,
    "--prune": NONE, "--scheme": STRING, "--si": NONE, "--sort": STRING,
    "--timefmt": STRING, "--version": NONE,
}

CAT_SAFE_FLAGS = {
    "-A": NONE, "-E": NONE, "-T": NONE, "-b": NONE, "-e": NONE, "-n": NONE,
    "-s": NONE, "-t": NONE, "-u": NONE, "-v": NONE,
    "--number": NONE, "--number-nonblank": NONE, "--show-all": NONE, "--show-ends": NONE,
    "--show-nonprinting": NONE, "--show-tabs": NONE, "--squeeze-blank": NONE,
}

HEAD_TAIL_SAFE_FLAGS = {
    "-c": NUMBER, "-n": NUMBER, "-q": NONE, "-v": NONE,
    "--bytes": NUMBER, "--lines": NUMBER, "--quiet": NONE, "--silent": NONE,
    "--verbose": NONE,
}

WC_SAFE_FLAGS = {
    "-L": NONE, "-c": NONE, "-l": NONE, "-m": NONE, "-w": NONE,
    "--bytes": NONE, "--chars": NONE, "--lines": NONE, "--max-line-length": NONE,
    "--words": NONE,
}

RG_SAFE_FLAGS = {
    "-A": NUMBER, "-B": NUMBER, "-C": NUMBER, "-F": NONE, "-H": NONE, "-L": NONE,
    "-N": NONE, "-S": NONE, "-T": STRING, "-c": NONE, "-e": STRING, "-g": STRING,
    "-h": NONE, "-i": NONE, "-l": NONE, "-m": NUMBER, "-n": NONE, "-s": NONE,
    "-t": STRING, "-u": NONE, "-v": NONE, "-w": NONE, "-x": NONE,
    "--after-context": NUMBER, "--before-context": NUMBER, "--case-sensitive": NONE,
    "--context": NUMBER, "--count": NONE, "--count-matches": NONE, "--files": NONE,
    "--files-with-matches": NONE, "--fixed-strings": NONE, "--follow": NONE,
    "--glob": STRING, "--hidden": NONE, "--ignore-case": NONE, "--ignore-file": STRING,
    "--iglob": STRING, "--invert-match": NONE, "--json": NONE, "--line-number": NONE,
    "--line-regexp": NONE, "--max-count": NUMBER, "--max-depth": NUMBER,
    "--max-filesize": STRING, "--no-filename": NONE, "--no-ignore": NONE,
    "--no-ignore-parent": NONE, "--no-ignore-vcs": NONE, "--no-line-number": NONE,
    "--pcre2": NONE, "--regexp": STRING, "--smart-case": NONE, "--sort": STRING,
    "--sortr": STRING, "--type": STRING, "--type-not": STRING, "--unrestricted": NONE,
    "--with-filename": NONE, "--word-regexp": NONE,
}

GREP_SAFE_FLAGS = {
    "-A": NUMBER, "-B": NUMBER, "-C": NUMBER, "-E": NONE, "-F": NONE, "-G": NONE,
    "-H": NONE, "-I": NONE, "-L": NONE, "-P": NONE, "-R": NONE, "-a": NONE,
    "-b": NONE, "-c": NONE, "-D": STRING, "-d": STRING, "-e": STRING, "-f": STRING,
    "-h": NONE, "-i": NONE, "-l": NONE, "-m": NUMBER, "-n": NONE, "-o": NONE,
    "-q": NONE, "-r": NONE, "-s": NONE, "-T": NONE, "-u": NONE, "-U": NONE,
    "-v": NONE, "-V": NONE, "-w": NONE, "-x": NONE, "-Z": NONE, "-z": NONE,
    "--after-context": NUMBER, "--basic-regexp": NONE, "--before-context": NUMBER,
    "--binary": NONE, "--binary-files": STRING, "--byte-offset": NONE, "--color": STRING,
    "--colour": STRING, "--context": NUMBER, "--count": NONE, "--dereference-recursive": NONE,
    "--directories": STRING, "--devices": STRING, "--exclude": STRING,
    "--exclude-dir": STRING, "--exclude-from": STRING, "--extended-regexp": NONE,
    "--file": STRING, "--files-with-matches": NONE, "--files-without-match": NONE,
    "--fixed-strings": NONE, "--group-separator": STRING, "--help": NONE,
    "--ignore-case": NONE, "--include": STRING, "--initial-tab": NONE,
    "--invert-match": NONE, "--label": STRING, "--line-buffered": NONE,
    "--line-number": NONE, "--line-regexp": NONE, "--max-count": NUMBER,
    "--no-group-separator": NONE, "--no-filename": NONE, "--no-ignore-case": NONE,
    "--no-messages": NONE, "--null": NONE, "--null-data": NONE, "--only-matching": NONE,
    "--perl-regexp": NONE, "--quiet": NONE, "--recursive": NONE, "--regexp": STRING,
    "--silent": NONE, "--text": NONE, "--unix-byte-offsets": NONE, "--version": NONE,
    "--with-filename": NONE, "--word-regexp": NONE,
}

SED_SAFE_FLAGS = {
    "-n": NONE, "--quiet": NONE, "--silent": NONE, "-E": NONE, "-r": NONE,
    "--regexp-extended": NONE, "--posix": NONE, "-z": NONE, "--zero-terminated": NONE,
}

XARGS_SAFE_FLAGS = {
    "-0": NONE, "-E": EOF, "-I": BRACE, "-L": NUMBER, "-P": NUMBER, "-d": CHAR,
    "-n": NUMBER, "-r": NONE, "-s": NUMBER, "-t": NONE, "-x": NONE,
}

FILE_SAFE_FLAGS = {
    "-0": NONE, "-F": STRING, "-L": NONE, "-b": NONE, "-c": NONE, "-f": STRING,
    "-h": NONE, "-i": NONE, "-k": NONE, "-l": NONE, "-m": STRING, "-n": NONE,
    "-p": NONE, "-r": NONE, "-s": NONE, "-v": NONE, "--apple": NONE, "--brief": NONE,
    "--check-encoding": NONE, "--dereference": NONE, "--exclude": STRING,
    "--exclude-quiet": STRING, "--help": NONE, "--keep-going": NONE, "--list": NONE,
    "--magic-file": STRING, "--mime": NONE, "--mime-encoding": NONE, "--mime-type": NONE,
    "--no-buffer": NONE, "--no-dereference": NONE, "--preserve-date": NONE,
    "--print0": NONE, "--raw": NONE, "--separator": STRING, "--special-files": NONE,
    "--version": NONE,
}

JQ_SAFE_FLAGS = {
    "-C": NONE, "-M": NONE, "-S": NONE, "-c": NONE, "-e": NONE, "-n": NONE,
    "-r": NONE, "-s": NONE, "--color-output": NONE, "--compact-output": NONE,
    "--exit-status": NONE, "--indent": NUMBER, "--monochrome-output": NONE,
    "--null-input": NONE, "--raw-output": NONE, "--slurp": NONE, "--sort-keys": NONE,
    "--tab": NONE,
}

FD_SAFE_FLAGS = {
    "-0": NONE, "-1": NONE, "-E": STRING, "-F": NONE, "-H": NONE, "-I": NONE,
    "-L": NONE, "-S": STRING, "-V": NONE, "-a": NONE, "-c": STRING, "-d": NUMBER,
    "-e": STRING, "-g": NONE, "-h": NONE, "-i": NONE, "-j": NUMBER, "-o": STRING,
    "-p": NONE, "-q": NONE, "-s": NONE, "-t": STRING, "--absolute-path": NONE,
    "--and": STRING, "--base-directory": STRING, "--batch-size": NUMBER,
    "--case-sensitive": NONE, "--changed-before": STRING, "--changed-within": STRING,
    "--color": STRING, "--exact-depth": NUMBER, "--exclude": STRING,
    "--extension": STRING, "--fixed-strings": NONE, "--follow": NONE,
    "--format": STRING, "--full-path": NONE, "--glob": NONE, "--help": NONE,
    "--hidden": NONE, "--hyperlink": STRING, "--ignore-case": NONE,
    "--ignore-file": STRING, "--max-buffer-time": STRING, "--max-depth": NUMBER,
    "--max-results": NUMBER, "--min-depth": NUMBER, "--no-ignore": NONE,
    "--no-ignore-parent": NONE, "--no-ignore-vcs": NONE, "--no-require-git": NONE,
    "--one-file-system": NONE, "--owner": STRING, "--path-separator": STRING,
    "--print0": NONE, "--prune": NONE, "--quiet": NONE, "--regex": NONE,
    "--search-path": STRING, "--show-errors": NONE, "--size": STRING,
    "--strip-cwd-prefix": NONE, "--threads": NUMBER, "--type": STRING,
    "--version": NONE,
}

SORT_SAFE_FLAGS = {
    "-C": NONE, "-M": NONE, "-R": NONE, "-S": STRING, "-V": NONE, "-b": NONE,
    "-c": NONE, "-d": NONE, "-f": NONE, "-g": NONE, "-h": NONE, "-i": NONE,
    "-k": STRING, "-m": NONE, "-n": NONE, "-r": NONE, "-s": NONE, "-t": STRING,
    "-u": NONE, "-z": NONE, "--batch-size": NUMBER, "--buffer-size": STRING,
    "--check": NONE, "--check-char-order": NONE, "--dictionary-order": NONE,
    "--field-separator": STRING, "--general-numeric-sort": NONE, "--help": NONE,
    "--human-numeric-sort": NONE, "--ignore-case": NONE, "--ignore-leading-blanks": NONE,
    "--ignore-nonprinting": NONE, "--key": STRING, "--merge": NONE,
    "--month-sort": NONE, "--numeric-sort": NONE, "--parallel": NUMBER,
    "--random-sort": NONE, "--reverse": NONE, "--sort": STRING, "--stable": NONE,
    "--unique": NONE, "--version": NONE, "--version-sort": NONE, "--zero-terminated": NONE,
}

BASE64_SAFE_FLAGS = {
    "-D": NONE, "-b": NUMBER, "-d": NONE, "-h": NONE, "-i": STRING, "-w": NUMBER,
    "--break": NUMBER, "--decode": NONE, "--help": NONE, "--ignore-garbage": NONE,
    "--input": STRING, "--version": NONE, "--wrap": NUMBER,
}

SHA_SUM_SAFE_FLAGS = {
    "-b": NONE, "-c": NONE, "-t": NONE, "-w": NONE, "-z": NONE, "--binary": NONE,
    "--check": NONE, "--help": NONE, "--ignore-missing": NONE, "--quiet": NONE,
    "--status": NONE, "--strict": NONE, "--tag": NONE, "--text": NONE,
    "--version": NONE, "--warn": NONE, "--zero": NONE,
}

DATE_SAFE_FLAGS = {
    "-I": NONE, "-R": NONE, "-d": STRING, "-r": STRING, "-u": NONE, "--date": STRING,
    "--debug": NONE, "--help": NONE, "--iso-8601": STRING, "--reference": STRING,
    "--rfc-3339": STRING, "--rfc-email": NONE, "--universal": NONE, "--utc": NONE,
    "--version": NONE,
}

HOSTNAME_SAFE_FLAGS = {
    "-A": NONE, "-V": NONE, "-a": NONE, "-d": NONE, "-f": NONE, "-h": NONE,
    "-i": NONE, "-I": NONE, "-s": NONE, "-v": NONE, "--alias": NONE, "--all-fqdns": NONE,
    "--all-ip-addresses": NONE, "--domain": NONE, "--fqdn": NONE, "--help": NONE,
    "--ip-address": NONE, "--long": NONE, "--short": NONE, "--verbose": NONE,
    "--version": NONE,
}

PS_SAFE_FLAGS = {
    "-A": NONE, "-C": STRING, "-F": NONE, "-G": STRING, "-H": NONE, "-L": NONE,
    "-N": NONE, "-T": NONE, "-U": STRING, "-V": NONE, "-a": NONE, "-c": NONE,
    "-d": NONE, "-e": NONE, "-f": NONE, "-g": STRING, "-j": NONE, "-l": NONE,
    "-m": NONE, "-n": STRING, "-p": STRING, "-q": STRING, "-s": STRING, "-t": STRING,
    "-u": STRING, "-w": NONE, "-ww": NONE, "-y": NONE, "--deselect": NONE,
    "--forest": NONE, "--headers": NONE, "--help": NONE, "--info": NONE,
    "--no-headers": NONE, "--pid": STRING, "--quick-pid": STRING, "--sid": STRING,
    "--sort": STRING, "--tty": STRING, "--user": STRING, "--version": NONE,
    "--width": NUMBER,
}

PYRIGHT_SAFE_FLAGS = {
    "--dependencies": NONE, "--level": STRING, "--outputjson": NONE,
    "--pythonplatform": STRING, "--pythonversion": STRING, "--stats": NONE,
    "--verbose": NONE, "--version": NONE, "--warnings": NONE,
}

MAN_SAFE_FLAGS = {
    "-S": STRING, "-a": NONE, "-d": NONE, "-f": NONE, "-h": NONE, "-k": NONE,
    "-s": STRING, "-w": NONE, "--all": NONE, "--apropos": NONE, "--whatis": NONE,
}

NETSTAT_SAFE_FLAGS = {
    "-I": STRING, "-L": NONE, "-a": NONE, "-f": STRING, "-g": NONE, "-i": NONE,
    "-l": NONE, "-m": NONE, "-n": NONE, "-r": NONE, "-s": NONE, "-v": NONE,
}

LSOF_SAFE_FLAGS = {
    "-?": NONE, "-A": STRING, "-C": NONE, "-E": NONE, "-F": NONE, "-H": NONE,
    "-K": NONE, "-L": NONE, "-N": NONE, "-O": NONE, "-P": NONE, "-Q": NONE,
    "-R": NONE, "-S": NONE, "-T": NONE, "-U": NONE, "-V": NONE, "-X": NONE,
    "-a": NONE, "-b": NONE, "-c": STRING, "-d": STRING, "-e": STRING, "-g": NONE,
    "-h": NONE, "-i": NONE, "-k": STRING, "-l": NONE, "-n": NONE, "-o": NONE,
    "-p": STRING, "-r": NONE, "-s": NONE, "-t": NONE, "-u": STRING, "-v": NONE,
    "-x": NONE,
}

PGREP_SAFE_FLAGS = {
    "-F": STRING, "-G": STRING, "-L": NONE, "-O": STRING, "-P": STRING, "-U": STRING,
    "-V": NONE, "-a": NONE, "-c": NONE, "-d": STRING, "-f": NONE, "-g": STRING,
    "-i": NONE, "-l": NONE, "-n": NONE, "-o": NONE, "-r": STRING, "-s": STRING,
    "-t": STRING, "-u": STRING, "-v": NONE, "-w": NONE, "-x": NONE, "--count": NONE,
    "--delimiter": STRING, "--euid": STRING, "--exact": NONE, "--full": NONE,
    "--group": STRING, "--help": NONE, "--ignore-case": NONE, "--inverse": NONE,
    "--lightweight": NONE, "--list-full": NONE, "--list-name": NONE,
    "--logpidfile": NONE, "--newest": NONE, "--ns": STRING, "--nslist": STRING,
    "--older": STRING, "--oldest": NONE, "--parent": STRING, "--pgroup": STRING,
    "--pidfile": STRING, "--runstates": STRING, "--session": STRING,
    "--terminal": STRING, "--uid": STRING, "--version": NONE,
}

TPUT_SAFE_FLAGS = {"-T": STRING, "-V": NONE, "-x": NONE}

SS_SAFE_FLAGS = {
    "-0": NONE, "-4": NONE, "-6": NONE, "-A": STRING, "-E": NONE, "-H": NONE,
    "-M": NONE, "-O": NONE, "-S": NONE, "-V": NONE, "-Z": NONE, "-a": NONE,
    "-b": NONE, "-d": NONE, "-e": NONE, "-f": STRING, "-h": NONE, "-i": NONE,
    "-l": NONE, "-m": NONE, "-n": NONE, "-o": NONE, "-p": NONE, "-s": NONE,
    "-t": NONE, "-u": NONE, "-w": NONE, "-x": NONE, "-z": NONE, "--all": NONE,
    "--bpf": NONE, "--cgroup": NONE, "--contexts": NONE, "--context": NONE,
    "--dccp": NONE, "--events": NONE, "--extended": NONE, "--family": STRING,
    "--help": NONE, "--inet-sockopt": NONE, "--info": NONE, "--ipv4": NONE,
    "--ipv6": NONE, "--listening": NONE, "--memory": NONE, "--mptcp": NONE,
    "--no-header": NONE, "--numeric": NONE, "--oneline": NONE, "--options": NONE,
    "--packet": NONE, "--processes": NONE, "--query": STRING, "--raw": NONE,
    "--sctp": NONE, "--socket": STRING, "--summary": NONE, "--tcp": NONE,
    "--tipc": NONE, "--tipcinfo": NONE, "--tos": NONE, "--udp": NONE,
    "--unix": NONE, "--version": NONE, "--vsock": NONE,
}

TEST_SAFE_FLAGS = {
    "-b": STRING, "-c": STRING, "-d": STRING, "-e": STRING, "-ef": STRING,
    "-eq": STRING, "-f": STRING, "-g": STRING, "-G": STRING, "-ge": STRING,
    "-gt": STRING, "-h": STRING, "-k": STRING, "-L": STRING, "-le": STRING,
    "-lt": STRING, "-N": STRING, "-n": STRING, "-ne": STRING, "-nt": STRING,
    "-O": STRING, "-ot": STRING, "-p": STRING, "-r": STRING, "-s": STRING,
    "-S": STRING, "-t": STRING, "-u": STRING, "-w": STRING, "-x": STRING, "-z": STRING,
}

DOCKER_LOGS_SAFE_FLAGS = {
    "-f": NONE, "-n": STRING, "-t": NONE, "--details": NONE, "--follow": NONE,
    "--since": STRING, "--tail": STRING, "--timestamps": NONE, "--until": STRING,
}

DOCKER_INSPECT_SAFE_FLAGS = {
    "-f": STRING, "-s": NONE, "--format": STRING, "--size": NONE, "--type": STRING,
}

GH_REPO_FLAGS = {"-R": STRING, "--repo": STRING}
GH_JSON_REPO_FLAGS = {**GH_REPO_FLAGS, "--json": STRING}
GH_LIMIT_REPO_FLAGS = {**GH_JSON_REPO_FLAGS, "-L": NUMBER, "--limit": NUMBER}


# ---------------------------------------------------------------------------
# Git flag tables
# ---------------------------------------------------------------------------
COMMON_GIT_REF_FLAGS = {"--all": NONE, "--branches": NONE, "--remotes": NONE, "--tags": NONE}
COMMON_GIT_DATE_FLAGS = {"--after": STRING, "--before": STRING, "--since": STRING, "--until": STRING}
COMMON_GIT_LOG_STYLE_FLAGS = {
    "--date": STRING, "--decorate": NONE, "--graph": NONE, "--no-decorate": NONE,
    "--oneline": NONE, "--relative-date": NONE,
}
COMMON_GIT_COUNT_FLAGS = {"-n": NUMBER, "--max-count": NUMBER}
COMMON_GIT_STAT_FLAGS = {
    "--name-only": NONE, "--name-status": NONE, "--numstat": NONE, "--shortstat": NONE,
    "--stat": NONE,
}
COMMON_GIT_COLOR_FLAGS = {"--color": NONE, "--no-color": NONE}
COMMON_GIT_PATCH_FLAGS = {
    "-p": NONE, "-s": NONE, "--no-ext-diff": NONE, "--no-patch": NONE, "--patch": NONE,
}

GIT_STATUS_SAFE_FLAGS = {
    "-b": NONE, "-M": STRING, "-s": NONE, "-u": STRING, "--ahead-behind": NONE,
    "--branch": NONE, "--column": NONE, "--find-renames": STRING,
    "--ignore-submodules": STRING, "--ignored": NONE, "--long": NONE,
    "--no-ahead-behind": NONE, "--no-column": NONE, "--no-renames": NONE,
    "--porcelain": NONE, "--renames": NONE, "--short": NONE,
    "--untracked-files": STRING, "--verbose": NONE, "-v": NONE,
}

GIT_DIFF_SAFE_FLAGS = {
    "-B": NONE, "-C": NONE, "-D": NONE, "-G": STRING, "-M": NONE, "-O": STRING,
    "-R": NONE, "-S": STRING, "-l": NONE, "-p": NONE, "-s": NONE, "-u": NONE,
    "--cached": NONE, "--check": NONE, "--color": NONE, "--color-words": NONE,
    "--diff-algorithm": STRING, "--diff-filter": STRING, "--dirstat": NONE,
    "--exit-code": NONE, "--find-copies": NONE, "--find-copies-harder": NONE,
    "--find-renames": NONE, "--full-index": NONE, "--function-context": NONE,
    "--histogram": NONE, "--ignore-all-space": NONE, "--ignore-blank-lines": NONE,
    "--ignore-space-at-eol": NONE, "--ignore-space-change": NONE,
    "--inter-hunk-context": NUMBER, "--irreversible-delete": NONE, "--minimal": NONE,
    "--name-only": NONE, "--name-status": NONE, "--no-ext-diff": NONE,
    "--no-index": NONE, "--no-renames": NONE, "--no-color": NONE, "--numstat": NONE,
    "--patch-with-stat": NONE, "--patience": NONE, "--pickaxe-all": NONE,
    "--pickaxe-regex": NONE, "--quiet": NONE, "--relative": STRING,
    "--shortstat": NONE, "--staged": NONE, "--stat": NONE, "--summary": NONE,
    "--word-diff": NONE, "--word-diff-regex": STRING, "--ws-error-highlight": STRING,
}

GIT_LOG_SAFE_FLAGS = {
    **COMMON_GIT_LOG_STYLE_FLAGS, **COMMON_GIT_REF_FLAGS, **COMMON_GIT_DATE_FLAGS,
    **COMMON_GIT_COUNT_FLAGS, **COMMON_GIT_STAT_FLAGS, **COMMON_GIT_COLOR_FLAGS,
    **COMMON_GIT_PATCH_FLAGS,
    "-G": STRING, "-S": STRING, "--abbrev-commit": NONE, "--ancestry-path": NONE,
    "--author": STRING, "--author-date-order": NONE, "--boundary": NONE,
    "--cherry-mark": NONE, "--cherry-pick": NONE, "--committer": STRING,
    "--date-order": NONE, "--dense": NONE, "--diff-filter": STRING,
    "--first-parent": NONE, "--format": STRING, "--full-history": NONE,
    "--grep": STRING, "--left-right": NONE, "--max-age": NUMBER, "--min-age": NUMBER,
    "--merges": NONE, "--no-max-parents": NONE, "--no-merges": NONE,
    "--no-min-parents": NONE, "--no-walk": NONE, "--pickaxe-all": NONE,
    "--pickaxe-regex": NONE, "--pretty": STRING, "--reverse": NONE,
    "--simplify-merges": NONE, "--skip": NUMBER, "--source": NONE, "--sparse": NONE,
    "--topo-order": NONE, "--walk-reflogs": NONE,
}

GIT_SHOW_SAFE_FLAGS = {
    **COMMON_GIT_LOG_STYLE_FLAGS, **COMMON_GIT_STAT_FLAGS, **COMMON_GIT_COLOR_FLAGS,
    **COMMON_GIT_PATCH_FLAGS,
    "-m": NONE, "--abbrev-commit": NONE, "--color-words": NONE, "--diff-filter": STRING,
    "--first-parent": NONE, "--format": STRING, "--pretty": STRING, "--quiet": NONE,
    "--raw": NONE, "--word-diff": NONE, "--word-diff-regex": STRING,
}

GIT_GREP_SAFE_FLAGS = {
    "-A": NUMBER, "-B": NUMBER, "-C": NUMBER, "-E": NONE, "-F": NONE, "-G": NONE,
    "-H": NONE, "-L": NONE, "-P": NONE, "-c": NONE, "-e": STRING, "-h": NONE,
    "-i": NONE, "-l": NONE, "-n": NONE, "-o": NONE, "-q": NONE, "-v": NONE,
    "-w": NONE, "--after-context": NUMBER, "--and": NONE, "--basic-regexp": NONE,
    "--break": NONE, "--cached": NONE, "--color": NONE, "--context": NUMBER,
    "--count": NONE, "--extended-regexp": NONE, "--files-with-matches": NONE,
    "--files-without-match": NONE, "--fixed-strings": NONE, "--full-name": NONE,
    "--heading": NONE, "--ignore-case": NONE, "--invert-match": NONE,
    "--line-number": NONE, "--max-depth": NUMBER, "--no-color": NONE,
    "--no-index": NONE, "--not": NONE, "--only-matching": NONE, "--or": NONE,
    "--perl-regexp": NONE, "--quiet": NONE, "--recurse-submodules": NONE,
    "--threads": NUMBER, "--untracked": NONE, "--word-regexp": NONE,
}

GIT_GLOBAL_NO_VALUE_FLAGS = frozenset({"--no-pager", "--paginate"})
GIT_GLOBAL_VALUE_FLAGS: frozenset[str] = frozenset()
GIT_GLOBAL_DANGEROUS_FLAGS = frozenset(
    {
        "-c", "-C", "--attr-source", "--bare", "--config-env", "--exec-path", "--git-dir",
        "--namespace", "--shallow-file", "--super-prefix", "--work-tree",
    }
)
GIT_ATTACHED_DANGEROUS_SHORT_FLAGS = ("-c", "-C")


# ---------------------------------------------------------------------------
# Policy model
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CommandPolicy:
    safe_flags: dict[str, str] = field(default_factory=dict)
    allow_any_args: bool = False
    command_only: bool = False
    respects_double_dash: bool = True
    allow_compact_numeric: bool = False
    callback: Callable[[list[str]], bool] | None = None
    regex: str | None = None


def _policy(
    safe_flags: dict[str, str] | None = None,
    *,
    allow_any_args: bool = False,
    command_only: bool = False,
    respects_double_dash: bool = True,
    allow_compact_numeric: bool = False,
    callback: Callable[[list[str]], bool] | None = None,
    regex: str | None = None,
) -> CommandPolicy:
    return CommandPolicy(
        safe_flags=dict(safe_flags or {}),
        allow_any_args=allow_any_args,
        command_only=command_only,
        respects_double_dash=respects_double_dash,
        allow_compact_numeric=allow_compact_numeric,
        callback=callback,
        regex=regex,
    )


# ---------------------------------------------------------------------------
# Callbacks: return True when the invocation is dangerous.
# ---------------------------------------------------------------------------
def _is_sed_in_place(word: str) -> bool:
    return word.startswith("-i") or word == "--in-place" or word.startswith("--in-place=")


_SED_QUIET_FLAGS = frozenset({"-n", "--quiet", "--silent"})
_SED_ZERO_FLAGS = frozenset({"-z", "--zero-terminated"})
_SED_PRINT = re.compile(r"^(?:[1-9]\d*(?:,[1-9]\d*)?)?p$")
_SED_SCRIPT_CONTROL = re.compile(r"[\x00-\x1f\x85\u2028\u2029]")
_SED_ESCAPES = frozenset("/\\&123456789.^$*+?()[]{}|")
_SED_SUBST_FLAGS = re.compile(r"^[gpimIM]*[1-9]?[gpimIM]*$")


def sed_dangerous(args: list[str]) -> bool:
    index = 0
    quiet = False
    zero = False
    while index < len(args):
        arg = args[index]
        if arg == "--":
            index += 1
            break
        if not arg.startswith("-"):
            break
        flags = [arg] if arg.startswith("--") else [f"-{flag}" for flag in arg[1:]]
        if not flags or any(flag not in SED_SAFE_FLAGS for flag in flags):
            return True
        quiet = quiet or any(flag in _SED_QUIET_FLAGS for flag in flags)
        zero = zero or any(flag in _SED_ZERO_FLAGS for flag in flags)
        index += 1
    script = args[index] if index < len(args) else ""
    if not script or _SED_SCRIPT_CONTROL.search(script):
        return True
    files = args[index + 1 :]
    if any(file.startswith("-") and file != "-" for file in files):
        return True
    if quiet and all(_SED_PRINT.match(part.strip()) for part in script.split(";")):
        return False
    return quiet or zero or bool(files) or not _readonly_sed_substitution(script)


def _readonly_sed_substitution(script: str) -> bool:
    if not script.startswith("s/") or ";" in script:
        return False
    delimiters = 0
    last = -1
    index = 2
    while index < len(script):
        character = script[index]
        if character == "\\":
            index += 1
            if index >= len(script) or script[index] not in _SED_ESCAPES:
                return False
        elif character == "/":
            delimiters += 1
            last = index
        index += 1
    return delimiters == 2 and bool(_SED_SUBST_FLAGS.match(script[last + 1 :]))


def _jq_dangerous_option(word: str) -> bool:
    return (
        word == "-f"
        or word.startswith("-f")
        or word == "-L"
        or word.startswith("-L")
        or word == "--argfile"
        or word.startswith("--argfile=")
        or word == "--from-file"
        or word.startswith("--from-file=")
        or word == "--library-path"
        or word.startswith("--library-path=")
        or word == "--rawfile"
        or word.startswith("--rawfile=")
        or word == "--run-tests"
        or word.startswith("--run-tests=")
        or word == "--slurpfile"
        or word.startswith("--slurpfile=")
    )


def _jq_filter_dangerous(filter_text: str) -> bool:
    return bool(
        re.search(r"\$ENV\b", filter_text)
        or re.search(r"(^|[^A-Za-z0-9_$.])env(?=$|[^A-Za-z0-9_])", filter_text)
        or re.search(r"(^|[^A-Za-z0-9_])(?:include|import)(?=$|[^A-Za-z0-9_])", filter_text)
    )


def jq_dangerous(args: list[str]) -> bool:
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg:
            index += 1
            continue
        if _jq_dangerous_option(arg):
            return True
        if arg == "--":
            return _jq_filter_dangerous(args[index + 1] if index + 1 < len(args) else "")
        if arg == "--indent":
            index += 2
            continue
        if arg.startswith("-"):
            index += 1
            continue
        return _jq_filter_dangerous(arg)
    return False


def date_dangerous(args: list[str]) -> bool:
    value_flags = {"-d", "--date", "-r", "--reference", "--rfc-3339"}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("--") and "=" in arg:
            index += 1
            continue
        if arg.startswith("-"):
            index += 2 if arg in value_flags else 1
            continue
        if not arg.startswith("+"):
            return True
        index += 1
    return False


def ps_dangerous(args: list[str]) -> bool:
    return any(
        not arg.startswith("-") and re.fullmatch(r"[a-zA-Z]*e[a-zA-Z]*", arg) is not None
        for arg in args
    )


def pyright_dangerous(args: list[str]) -> bool:
    return any(arg in {"--watch", "-w"} for arg in args)


def man_dangerous(args: list[str]) -> bool:
    apropos_flags = {"-k", "-f", "--apropos", "--whatis"}
    value_flags = {"-S", "-s"}
    is_apropos = False
    after_double_dash = False
    index = 0
    while index < len(args):
        arg = args[index]
        if not after_double_dash and arg == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and arg.startswith("-") and arg != "-":
            if arg in apropos_flags:
                is_apropos = True
            if arg in value_flags:
                index += 1
            index += 1
            continue
        after_double_dash = True
        if "/" in arg:
            return not is_apropos
        index += 1
    return False


def lsof_dangerous(args: list[str]) -> bool:
    for index, arg in enumerate(args):
        if arg == "+m" or arg.startswith("+m"):
            return True
        if re.match(r"^-[a-zA-Z]*i\S*@", arg):
            host = arg.split("@", 1)[1].split(":")[0]
            if re.search(r"[a-zA-Z]", host):
                return True
        if re.match(r"^-[a-zA-Z]*i$", arg):
            following = args[index + 1] if index + 1 < len(args) else ""
            if "@" in following:
                host = following.split("@", 1)[1].split(":")[0]
                if re.search(r"[a-zA-Z]", host):
                    return True
    return False


_TPUT_DANGEROUS = frozenset(
    {
        "clear", "flash", "if", "init", "iprog", "is1", "is2", "is3", "mc0", "mc4",
        "mc5", "mc5i", "mc5p", "pfkey", "pfloc", "pfx", "pfxl", "reset", "rf",
        "rmcup", "rs1", "rs2", "rs3", "smcup",
    }
)


def tput_dangerous(args: list[str]) -> bool:
    after_double_dash = False
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and arg.startswith("-"):
            if arg == "-S":
                return True
            if not arg.startswith("--") and len(arg) > 2 and "S" in arg:
                return True
            index += 2 if arg == "-T" else 1
            continue
        if arg in _TPUT_DANGEROUS:
            return True
        index += 1
    return False


def ss_dangerous(args: list[str]) -> bool:
    keyword = re.compile(
        r"^(dst|src|dport|sport|and|or|not|eq|ne|ge|le|gt|lt|autobound|state|exclude|"
        r"dev|fwmark|cgroup)$"
    )
    value_keyword = re.compile(r"^(state|exclude|dport|sport|dev|fwmark|cgroup)$")
    value_flag = re.compile(r"^(-f|--family|-A|--query|--socket)$")
    positional: list[str] = []
    after_double_dash = False
    index = 0
    while index < len(args):
        arg = args[index]
        if not after_double_dash and arg == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and arg.startswith("-"):
            if value_flag.match(arg):
                index += 1
            index += 1
            continue
        positional.append(arg)
        index += 1
    tokens = [token for token in re.split(r"[\s()=!<>&|,]+", " ".join(positional)) if token]
    skip_value = False
    for token in tokens:
        if skip_value:
            skip_value = False
            continue
        if keyword.match(token):
            skip_value = bool(value_keyword.match(token))
            continue
        if re.search(r"[g-zG-Z]", token) or (
            re.search(r"[a-fA-F]", token) and ("." in token or ":" not in token)
        ):
            return True
    return False


_TEST_NUMERIC = frozenset({"-eq", "-ne", "-lt", "-le", "-gt", "-ge"})
_TEST_NUMBER = re.compile(r"^-?(0[xX][0-9a-fA-F]+|[0-9]+#[0-9a-zA-Z]+|[0-9]+)$")


def test_dangerous(args: list[str]) -> bool:
    if any(arg in {"-v", "-R", "-a", "-o"} or "[" in arg for arg in args):
        return True
    for index, arg in enumerate(args):
        if arg in _TEST_NUMERIC:
            for value in (args[index - 1] if index else None, args[index + 1] if index + 1 < len(args) else None):
                if value is not None and not _TEST_NUMBER.match(value):
                    return True
        if arg == "-t":
            value = args[index + 1] if index + 1 < len(args) else None
            if value is not None and not _TEST_NUMBER.match(value):
                return True
    return False


XARGS_TARGET_COMMANDS = frozenset(
    {"echo", "printf", "wc", "grep", "egrep", "fgrep", "head", "tail"}
)
_XARGS_VALUE_FLAGS = frozenset({"-I", "-n", "-P", "-L", "-s", "-E", "-d"})


def xargs_dangerous(args: list[str]) -> bool:
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg:
            index += 1
            continue
        if arg == "--" and index + 1 < len(args):
            index += 1
            arg = args[index]
        if arg.startswith("-") and arg != "-":
            if arg in _XARGS_VALUE_FLAGS:
                index += 1
            index += 1
            continue
        return arg not in XARGS_TARGET_COMMANDS
    return False


def gh_dangerous(args: list[str]) -> bool:
    for arg in args:
        if not arg:
            continue
        value = arg
        if arg.startswith("-"):
            if "=" not in arg:
                continue
            value = arg.split("=", 1)[1]
            if not value:
                continue
        if "/" not in value and "://" not in value and "@" not in value:
            continue
        if "://" in value or "@" in value:
            return True
        if value.count("/") >= 2:
            return True
    return False


_DOCKER_DANGEROUS_GLOBAL = (
    "-H", "-c", "--config", "--context", "--host", "--tlscacert", "--tlscert", "--tlskey",
)
_DOCKER_DANGEROUS_SHORT = frozenset(
    flag[1] for flag in _DOCKER_DANGEROUS_GLOBAL if len(flag) == 2
)


def has_dangerous_docker_option(args: list[str]) -> bool:
    for arg in args:
        if any(
            arg == flag
            or arg.startswith(f"{flag}=")
            or (len(flag) == 2 and len(arg) > 2 and arg.startswith(flag))
            for flag in _DOCKER_DANGEROUS_GLOBAL
        ):
            return True
        match = re.match(r"^-([A-Za-z]+)", arg)
        if match and len(match.group(1)) >= 2:
            if any(flag in _DOCKER_DANGEROUS_SHORT for flag in match.group(1)):
                return True
    return False


# Git callbacks -----------------------------------------------------------------
def git_revision_format_dangerous(args: list[str]) -> bool:
    for index, arg in enumerate(args):
        value = arg.split("=", 1)[1] if "=" in arg else (args[index + 1] if index + 1 < len(args) else "")
        if (
            arg in {"--format", "--pretty"}
            or arg.startswith("--format=")
            or arg.startswith("--pretty=")
        ) and value:
            if re.search(r"%[-+ ]?G|%\(\*?signature", value):
                return True
    return False


def git_reflog_dangerous(args: list[str]) -> bool:
    first = next((arg for arg in args if arg and not arg.startswith("-")), None)
    if first and first not in {"show", "list"}:
        return True
    return any(arg in {"expire", "delete", "exists", "drop", "write"} for arg in args)


def git_ls_remote_dangerous(args: list[str]) -> bool:
    after_double_dash = False
    index = 0
    while index < len(args):
        arg = args[index]
        if not after_double_dash and arg == "--":
            after_double_dash = True
            index += 1
            continue
        if not after_double_dash and (arg.startswith("-") or not arg):
            if arg == "--sort":
                index += 1
            index += 1
            continue
        return True
    return False


def git_remote_show_dangerous(args: list[str]) -> bool:
    double_dash = args.index("--") if "--" in args else -1
    option_args = args if double_dash == -1 else args[:double_dash]
    positional = ([] if double_dash == -1 else args[double_dash + 1 :]) + [
        arg for arg in option_args if arg != "-n"
    ]
    if "-n" not in option_args:
        return True
    if len(positional) != 1:
        return True
    return re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_-]*", positional[0]) is None


def _git_list_like_dangerous(args: list[str], value_flags: frozenset[str]) -> bool:
    has_list = False
    after_double_dash = False
    previous_flag = ""
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg:
            index += 1
            continue
        if arg == "--" and not after_double_dash:
            after_double_dash = True
            previous_flag = ""
            index += 1
            continue
        if not after_double_dash and arg.startswith("-"):
            if arg == "--list" or arg == "-l" or (arg[0] == "-" and arg[1:2] != "-" and "l" in arg[1:]):
                has_list = True
            previous_flag = arg.split("=", 1)[0] if "=" in arg else arg
            if "=" not in arg and previous_flag in value_flags:
                index += 1
            index += 1
            continue
        if not has_list and previous_flag not in {"--merged", "--no-merged"}:
            return True
        index += 1
    return False


def git_tag_dangerous(args: list[str]) -> bool:
    return _git_list_like_dangerous(
        args,
        frozenset({"--contains", "--no-contains", "--merged", "--no-merged", "--points-at", "--sort", "--format", "-n"}),
    )


def git_branch_dangerous(args: list[str]) -> bool:
    return _git_list_like_dangerous(
        args, frozenset({"--contains", "--no-contains", "--points-at", "--sort"})
    )


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------
READONLY_ALLOW_ANY_ARG_COMMANDS = frozenset(
    {
        "basename", "cal", "cat", "cmp", "column", "comm", "cut", "df", "diff",
        "dirname", "du", "expand", "expr", "false", "fmt", "fold", "free", "groups",
        "head", "hexdump", "id", "locale", "nl", "nproc", "numfmt", "od", "paste",
        "pr", "readlink", "realpath", "rev", "seq", "sleep", "stat", "strings", "tac",
        "tail", "tr", "true", "tsort", "type", "uname", "unexpand", "uptime", "wc",
        "which",
    }
)

READONLY_ALLOW_ANY_ARG_COMMAND_PREFIXES = (("docker", "images"), ("docker", "ps"))

COMMAND_POLICIES: dict[str, CommandPolicy] = {
    "alias": _policy(command_only=True),
    "base64": _policy(BASE64_SAFE_FLAGS, respects_double_dash=False),
    "cat": _policy(CAT_SAFE_FLAGS),
    "cd": _policy(allow_any_args=True),
    "cut": _policy(allow_any_args=True),
    "date": _policy(DATE_SAFE_FLAGS, callback=date_dangerous),
    "diff": _policy(allow_any_args=True),
    "dirname": _policy(allow_any_args=True),
    "du": _policy(allow_any_args=True),
    "echo": _policy(allow_any_args=True),
    "egrep": _policy(GREP_SAFE_FLAGS),
    "false": _policy(command_only=True),
    "fd": _policy(FD_SAFE_FLAGS),
    "fdfind": _policy(FD_SAFE_FLAGS),
    "file": _policy(FILE_SAFE_FLAGS),
    "find": _policy(allow_any_args=True),
    "fgrep": _policy(GREP_SAFE_FLAGS),
    "grep": _policy(GREP_SAFE_FLAGS),
    "head": _policy(HEAD_TAIL_SAFE_FLAGS),
    "help": _policy({"-d": NONE, "-m": NONE, "-s": NONE}),
    "hexdump": _policy(allow_any_args=True),
    "hostname": _policy(
        HOSTNAME_SAFE_FLAGS,
        regex=r"^hostname(?:\s+(?:-[a-zA-Z]|--[a-zA-Z-]+))*\s*$",
    ),
    "id": _policy(allow_any_args=True),
    "jq": _policy(JQ_SAFE_FLAGS, callback=jq_dangerous),
    "lsof": _policy(LSOF_SAFE_FLAGS, callback=lsof_dangerous),
    "ls": _policy(allow_any_args=True),
    "man": _policy(MAN_SAFE_FLAGS, callback=man_dangerous),
    "md5sum": _policy(SHA_SUM_SAFE_FLAGS),
    "netstat": _policy(NETSTAT_SAFE_FLAGS),
    "nl": _policy(allow_any_args=True),
    "pgrep": _policy(PGREP_SAFE_FLAGS),
    "ps": _policy(PS_SAFE_FLAGS, callback=ps_dangerous),
    "pyright": _policy(PYRIGHT_SAFE_FLAGS, respects_double_dash=False, callback=pyright_dangerous),
    "printf": _policy(allow_any_args=True),
    "pwd": _policy(command_only=True),
    "readlink": _policy(allow_any_args=True),
    "realpath": _policy(allow_any_args=True),
    "rev": _policy(allow_any_args=True),
    "rg": _policy(RG_SAFE_FLAGS),
    "sed": _policy(SED_SAFE_FLAGS, callback=sed_dangerous),
    "sha1sum": _policy(SHA_SUM_SAFE_FLAGS),
    "sha256sum": _policy(SHA_SUM_SAFE_FLAGS),
    "sort": _policy(SORT_SAFE_FLAGS),
    "ss": _policy(SS_SAFE_FLAGS, callback=ss_dangerous),
    "stat": _policy(allow_any_args=True),
    "strings": _policy(allow_any_args=True),
    "tail": _policy(HEAD_TAIL_SAFE_FLAGS),
    "test": _policy(TEST_SAFE_FLAGS, respects_double_dash=False, callback=test_dangerous),
    "tput": _policy(TPUT_SAFE_FLAGS, callback=tput_dangerous),
    "tr": _policy(allow_any_args=True),
    "tree": _policy(TREE_SAFE_FLAGS),
    "true": _policy(command_only=True),
    "uniq": _policy(allow_any_args=True),
    "uname": _policy(allow_any_args=True),
    "wc": _policy(WC_SAFE_FLAGS),
    "which": _policy(allow_any_args=True),
    "whoami": _policy(command_only=True),
    "xargs": _policy(XARGS_SAFE_FLAGS, callback=xargs_dangerous),
    "[": _policy(TEST_SAFE_FLAGS, respects_double_dash=False, callback=test_dangerous),
    "[[": _policy(TEST_SAFE_FLAGS, respects_double_dash=False, callback=test_dangerous),
}

GIT_READONLY_POLICIES: dict[str, CommandPolicy] = {
    "git blame": _policy(
        {
            **COMMON_GIT_COLOR_FLAGS,
            "-C": NONE, "-L": STRING, "-M": NONE, "-e": NONE, "-f": NONE, "-l": NONE,
            "-n": NONE, "-p": NONE, "-s": NONE, "-t": NONE, "-w": NONE,
            "--abbrev": NUMBER, "--date": STRING, "--ignore-rev": STRING,
            "--ignore-revs-file": STRING, "--incremental": NONE, "--line-porcelain": NONE,
            "--porcelain": NONE, "--root": NONE, "--score-debug": NONE,
            "--show-email": NONE, "--show-name": NONE, "--show-number": NONE,
            "--show-stats": NONE,
        }
    ),
    "git branch": _policy(
        {
            "-a": NONE, "-i": NONE, "-l": NONE, "-r": NONE, "-v": NONE, "-vv": NONE,
            "--abbrev": NUMBER, "--all": NONE, "--color": NONE, "--column": NONE,
            "--contains": STRING, "--ignore-case": NONE, "--list": NONE,
            "--merged": NONE, "--no-abbrev": NONE, "--no-color": NONE,
            "--no-column": NONE, "--no-contains": STRING, "--no-merged": NONE,
            "--points-at": STRING, "--remotes": NONE, "--show-current": NONE,
            "--sort": STRING, "--verbose": NONE,
        },
        callback=git_branch_dangerous,
    ),
    "git cat-file": _policy(
        {"-e": NONE, "-p": NONE, "-s": NONE, "-t": NONE, "--allow-undetermined-type": NONE, "--batch-check": NONE}
    ),
    "git config --get": _policy(
        {
            "-z": NONE, "--bool": NONE, "--bool-or-int": NONE, "--default": STRING,
            "--expiry-date": NONE, "--global": NONE, "--int": NONE, "--local": NONE,
            "--name-only": NONE, "--null": NONE, "--path": NONE, "--show-origin": NONE,
            "--show-scope": NONE, "--system": NONE, "--type": STRING, "--worktree": NONE,
        }
    ),
    "git describe": _policy(
        {
            "--abbrev": NUMBER, "--always": NONE, "--broken": NONE, "--candidates": NUMBER,
            "--contains": NONE, "--dirty": NONE, "--exclude": STRING,
            "--exact-match": NONE, "--first-match": NONE, "--long": NONE,
            "--match": STRING, "--tags": NONE,
        }
    ),
    "git diff": _policy(GIT_DIFF_SAFE_FLAGS),
    "git for-each-ref": _policy(
        {
            "--contains": STRING, "--count": NUMBER, "--format": STRING, "--merged": STRING,
            "--no-contains": STRING, "--no-merged": STRING, "--points-at": STRING,
            "--sort": STRING,
        },
        callback=git_revision_format_dangerous,
    ),
    "git grep": _policy(GIT_GREP_SAFE_FLAGS),
    "git log": _policy(
        GIT_LOG_SAFE_FLAGS,
        allow_compact_numeric=True,
        callback=git_revision_format_dangerous,
    ),
    "git ls-files": _policy(
        {
            "-c": NONE, "-d": NONE, "-f": NONE, "-i": NONE, "-k": NONE, "-m": NONE,
            "-o": NONE, "-s": NONE, "-t": NONE, "-u": NONE, "-v": NONE, "-x": STRING,
            "-X": STRING, "-z": NONE, "--abbrev": NUMBER, "--cached": NONE,
            "--debug": NONE, "--deleted": NONE, "--directory": NONE, "--eol": NONE,
            "--error-unmatch": NONE, "--exclude": STRING, "--exclude-from": STRING,
            "--exclude-per-directory": STRING, "--exclude-standard": NONE,
            "--full-name": NONE, "--ignored": NONE, "--killed": NONE, "--modified": NONE,
            "--no-empty-directory": NONE, "--others": NONE, "--recurse-submodules": NONE,
            "--stage": NONE, "--unmerged": NONE,
        }
    ),
    "git ls-remote": _policy(
        {
            "-b": NONE, "-h": NONE, "-q": NONE, "-t": NONE, "--branches": NONE,
            "--exit-code": NONE, "--get-url": NONE, "--heads": NONE, "--quiet": NONE,
            "--refs": NONE, "--sort": STRING, "--symref": NONE, "--tags": NONE,
        },
        callback=git_ls_remote_dangerous,
    ),
    "git merge-base": _policy(
        {"--all": NONE, "--fork-point": NONE, "--independent": NONE, "--is-ancestor": NONE, "--octopus": NONE}
    ),
    "git reflog": _policy(
        {
            **COMMON_GIT_LOG_STYLE_FLAGS, **COMMON_GIT_REF_FLAGS, **COMMON_GIT_DATE_FLAGS,
            **COMMON_GIT_COUNT_FLAGS, **COMMON_GIT_STAT_FLAGS, "--grep": STRING,
        },
        callback=git_reflog_dangerous,
    ),
    "git remote show": _policy({"-n": NONE}, callback=git_remote_show_dangerous),
    "git remote": _policy(
        {"-v": NONE, "--verbose": NONE},
        callback=lambda args: any(arg not in {"-v", "--verbose"} for arg in args),
    ),
    "git rev-list": _policy(
        {
            **COMMON_GIT_REF_FLAGS, **COMMON_GIT_DATE_FLAGS, **COMMON_GIT_COUNT_FLAGS,
            "--abbrev": NUMBER, "--abbrev-commit": NONE, "--ancestry-path": NONE,
            "--count": NONE, "--dense": NONE, "--first-parent": NONE, "--format": STRING,
            "--full-history": NONE, "--graph": NONE, "--grep": STRING,
            "--max-age": NUMBER, "--max-parents": NUMBER, "--merges": NONE,
            "--min-age": NUMBER, "--min-parents": NUMBER, "--no-max-parents": NONE,
            "--no-merges": NONE, "--no-min-parents": NONE, "--oneline": NONE,
            "--pretty": STRING, "--reverse": NONE, "--skip": NUMBER, "--source": NONE,
            "--sparse": NONE, "--walk-reflogs": NONE,
        },
        callback=git_revision_format_dangerous,
    ),
    "git rev-parse": _policy(
        {
            "--abbrev-ref": NONE, "--absolute-git-dir": NONE, "--git-common-dir": NONE,
            "--git-dir": NONE, "--is-bare-repository": NONE, "--is-inside-git-dir": NONE,
            "--is-inside-work-tree": NONE, "--is-shallow-repository": NONE,
            "--is-shallow-update": NONE, "--path-prefix": NONE, "--short": STRING,
            "--show-cdup": NONE, "--show-prefix": NONE,
            "--show-superproject-working-tree": NONE, "--show-toplevel": NONE,
            "--symbolic": NONE, "--symbolic-full-name": NONE, "--verify": NONE,
        }
    ),
    "git shortlog": _policy(
        {
            **COMMON_GIT_REF_FLAGS, **COMMON_GIT_DATE_FLAGS,
            "-c": NONE, "-e": NONE, "-n": NONE, "-s": NONE, "--author": STRING,
            "--committer": NONE, "--email": NONE, "--format": STRING, "--group": STRING,
            "--no-merges": NONE, "--numbered": NONE, "--summary": NONE,
        },
        callback=git_revision_format_dangerous,
    ),
    "git show": _policy(GIT_SHOW_SAFE_FLAGS, callback=git_revision_format_dangerous),
    "git stash list": _policy(
        {**COMMON_GIT_LOG_STYLE_FLAGS, **COMMON_GIT_REF_FLAGS, **COMMON_GIT_COUNT_FLAGS}
    ),
    "git stash show": _policy(
        {
            **COMMON_GIT_STAT_FLAGS, **COMMON_GIT_COLOR_FLAGS, **COMMON_GIT_PATCH_FLAGS,
            "--abbrev": NUMBER, "--diff-filter": STRING, "--word-diff": NONE,
            "--word-diff-regex": STRING,
        }
    ),
    "git status": _policy(GIT_STATUS_SAFE_FLAGS),
    "git tag": _policy(
        {
            "-i": NONE, "-l": NONE, "-n": NUMBER, "--column": NONE, "--contains": STRING,
            "--format": STRING, "--ignore-case": NONE, "--list": NONE,
            "--merged": STRING, "--no-column": NONE, "--no-contains": STRING,
            "--no-merged": STRING, "--points-at": STRING, "--sort": STRING,
        },
        callback=git_tag_dangerous,
    ),
    "git worktree list": _policy(
        {"-v": NONE, "--expire": STRING, "--porcelain": NONE, "--verbose": NONE}
    ),
}

MULTIWORD_POLICIES: dict[str, CommandPolicy] = {
    "docker inspect": _policy(DOCKER_INSPECT_SAFE_FLAGS, callback=lambda args: has_dangerous_docker_option(args)),
    "docker logs": _policy(DOCKER_LOGS_SAFE_FLAGS, callback=lambda args: has_dangerous_docker_option(args)),
    "gh auth status": _policy(
        {"-a": NONE, "-h": STRING, "--active": NONE, "--hostname": STRING, "--json": STRING},
        callback=gh_dangerous,
    ),
    "gh issue list": _policy(
        {
            **GH_LIMIT_REPO_FLAGS, "-s": STRING, "--app": STRING, "--assignee": STRING,
            "--author": STRING, "--label": STRING, "--milestone": STRING,
            "--search": STRING, "--state": STRING,
        },
        callback=gh_dangerous,
    ),
    "gh issue status": _policy(GH_JSON_REPO_FLAGS, callback=gh_dangerous),
    "gh issue view": _policy({**GH_JSON_REPO_FLAGS, "--comments": NONE}, callback=gh_dangerous),
    "gh label list": _policy(
        {**GH_LIMIT_REPO_FLAGS, "-S": STRING, "--order": STRING, "--search": STRING, "--sort": STRING},
        callback=gh_dangerous,
    ),
    "gh pr checks": _policy(
        {
            **GH_JSON_REPO_FLAGS, "--fail-fast": NONE, "--interval": NUMBER,
            "--required": NONE, "--watch": NONE,
        },
        callback=gh_dangerous,
    ),
    "gh pr diff": _policy(
        {**GH_REPO_FLAGS, "--color": STRING, "--name-only": NONE, "--patch": NONE},
        callback=gh_dangerous,
    ),
    "gh pr list": _policy(
        {
            **GH_LIMIT_REPO_FLAGS, "-s": STRING, "--app": STRING, "--assignee": STRING,
            "--author": STRING, "--base": STRING, "--draft": NONE, "--head": STRING,
            "--label": STRING, "--search": STRING, "--state": STRING,
        },
        callback=gh_dangerous,
    ),
    "gh pr status": _policy(
        {**GH_JSON_REPO_FLAGS, "-c": NONE, "--conflict-status": NONE}, callback=gh_dangerous
    ),
    "gh pr view": _policy({**GH_JSON_REPO_FLAGS, "--comments": NONE}, callback=gh_dangerous),
    "gh release list": _policy(
        {
            **GH_LIMIT_REPO_FLAGS, "-O": STRING, "--exclude-drafts": NONE,
            "--exclude-pre-releases": NONE, "--order": STRING,
        },
        callback=gh_dangerous,
    ),
    "gh release view": _policy(GH_JSON_REPO_FLAGS, callback=gh_dangerous),
    "gh repo view": _policy({"--json": STRING}, callback=gh_dangerous),
    "gh run list": _policy(
        {
            **GH_LIMIT_REPO_FLAGS, "-b": STRING, "-c": STRING, "-e": STRING, "-s": STRING,
            "-u": STRING, "-w": STRING, "--branch": STRING, "--commit": STRING,
            "--created": STRING, "--event": STRING, "--status": STRING, "--user": STRING,
            "--workflow": STRING,
        },
        callback=gh_dangerous,
    ),
    "gh run view": _policy(
        {
            **GH_JSON_REPO_FLAGS, "-a": NUMBER, "-j": STRING, "-v": NONE, "--attempt": NUMBER,
            "--exit-status": NONE, "--job": STRING, "--log": NONE, "--log-failed": NONE,
            "--verbose": NONE,
        },
        callback=gh_dangerous,
    ),
    "gh search code": _policy(
        {
            **GH_LIMIT_REPO_FLAGS, "--extension": STRING, "--filename": STRING,
            "--language": STRING, "--match": STRING, "--owner": STRING, "--size": STRING,
        }
    ),
    "gh search commits": _policy(
        {
            **GH_LIMIT_REPO_FLAGS, "--author": STRING, "--author-date": STRING,
            "--author-email": STRING, "--author-name": STRING, "--committer": STRING,
            "--committer-date": STRING, "--committer-email": STRING, "--committer-name": STRING,
            "--hash": STRING, "--merge": NONE, "--order": STRING, "--owner": STRING,
            "--parent": STRING, "--sort": STRING, "--tree": STRING, "--visibility": STRING,
        }
    ),
    "gh search issues": _policy(
        {
            **GH_LIMIT_REPO_FLAGS, "-R": STRING, "--app": STRING, "--assignee": STRING,
            "--author": STRING, "--closed": STRING, "--commenter": STRING,
            "--comments": STRING, "--created": STRING, "--include-prs": NONE,
            "--interactions": STRING, "--involves": STRING, "--label": STRING,
            "--language": STRING, "--locked": NONE, "--match": STRING, "--mentions": STRING,
            "--milestone": STRING, "--no-assignee": NONE, "--no-label": NONE,
            "--no-milestone": NONE, "--no-project": NONE, "--order": STRING,
            "--owner": STRING, "--project": STRING, "--reactions": STRING, "--sort": STRING,
            "--state": STRING, "--team-mentions": STRING, "--updated": STRING,
            "--visibility": STRING,
        }
    ),
    "gh search prs": _policy(
        {
            **GH_LIMIT_REPO_FLAGS, "-B": STRING, "-H": STRING, "-R": STRING,
            "--app": STRING, "--assignee": STRING, "--author": STRING, "--base": STRING,
            "--checks": STRING, "--closed": STRING, "--commenter": STRING,
            "--comments": STRING, "--created": STRING, "--draft": NONE, "--head": STRING,
            "--interactions": STRING, "--involves": STRING, "--label": STRING,
            "--language": STRING, "--locked": NONE, "--match": STRING, "--mentions": STRING,
            "--merged": NONE, "--merged-at": STRING, "--milestone": STRING,
            "--no-assignee": NONE, "--no-label": NONE, "--no-milestone": NONE,
            "--no-project": NONE, "--order": STRING, "--owner": STRING, "--project": STRING,
            "--reactions": STRING, "--review": STRING, "--review-requested": STRING,
            "--reviewed-by": STRING, "--sort": STRING, "--state": STRING,
            "--team-mentions": STRING, "--updated": STRING, "--visibility": STRING,
        }
    ),
    "gh search repos": _policy(
        {
            **GH_LIMIT_REPO_FLAGS, "--archived": NONE, "--created": STRING,
            "--followers": STRING, "--forks": STRING, "--good-first-issues": STRING,
            "--help-wanted-issues": STRING, "--include-forks": STRING, "--language": STRING,
            "--license": STRING, "--match": STRING, "--number-topics": STRING,
            "--order": STRING, "--owner": STRING, "--size": STRING, "--sort": STRING,
            "--stars": STRING, "--topic": STRING, "--updated": STRING,
            "--visibility": STRING,
        }
    ),
    "gh workflow list": _policy(
        {**GH_LIMIT_REPO_FLAGS, "-a": NONE, "--all": NONE}, callback=gh_dangerous
    ),
    "gh workflow view": _policy(
        {**GH_REPO_FLAGS, "-r": STRING, "-y": NONE, "--ref": STRING, "--yaml": NONE},
        callback=gh_dangerous,
    ),
}


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
_UNC_PATTERN = re.compile(r"^(?://|\\\\)[^/\\]")
_DEVICE_PATTERN = re.compile(r"^/dev/(?:tcp|udp)/")
_FIND_WRITE_OPTIONS = frozenset(
    {
        "-delete", "-exec", "-execdir", "-files0-from", "-fls", "-fprint", "-fprint0",
        "-fprintf", "-ok", "-okdir",
    }
)
_FIND_VALUE_OPTIONS = frozenset(
    {
        "-Bmin", "-Bnewer", "-Btime", "-D", "-amin", "-anewer", "-atime", "-cmin",
        "-cnewer", "-context", "-ctime", "-f", "-flags", "-fstype", "-gid", "-group",
        "-ilname", "-iname", "-inum", "-ipath", "-iregex", "-iwholename", "-lname",
        "-links", "-maxdepth", "-mindepth", "-mmin", "-mnewer", "-mtime", "-name",
        "-newer", "-path", "-perm", "-printf", "-regex", "-regextype", "-samefile",
        "-size", "-type", "-used", "-user", "-wholename", "-xattrname", "-xtype", "-uid",
    }
)
_FIND_NEWER_PATTERN = re.compile(r"^-newer[aBcm][aBcmt]$")
_EXACT_ARGV_COMMANDS = (
    ("ip", "addr"),
    ("node", "-v"),
    ("node", "--version"),
    ("python", "--version"),
    ("python3", "--version"),
)


def _strip_safe_wrappers(argv: list[str]) -> list[str]:
    stripped = list(argv)
    while stripped:
        if stripped[0] == "command":
            index = 1
            while index < len(stripped) and re.fullmatch(r"-p+", stripped[index] or ""):
                index += 1
            if index < len(stripped) and stripped[index] == "--":
                index += 1
            if index >= len(stripped) or stripped[index].startswith("-"):
                return stripped
            stripped = stripped[index:]
            continue
        if stripped[0] == "builtin":
            index = 2 if len(stripped) > 1 and stripped[1] == "--" else 1
            if index >= len(stripped):
                return stripped
            stripped = stripped[index:]
            continue
        if stripped[0] == "noglob":
            if len(stripped) <= 1:
                return stripped
            stripped = stripped[1:]
            continue
        return stripped
    return stripped


def _is_unsafe_unc(value: str) -> bool:
    return _UNC_PATTERN.match(value) is not None


def _redirects_allowed(invocation: Invocation) -> bool:
    for redirect in invocation.redirects:
        target = redirect.target
        if _DEVICE_PATTERN.match(target):
            return False
        if redirect.operator in {">&", "&>"} and target.isdigit():
            continue
        if target == "/dev/null":
            continue
        if redirect.operator in SAFE_INPUT_REDIRECTS:
            if _is_unsafe_unc(target):
                return False
            continue
        return False
    return True


def _env_assignments_allowed(invocation: Invocation) -> bool:
    return all(name in SAFE_ENV_ASSIGNMENTS for name, _value in invocation.env_assignments)


def _parse_option_word(word: str) -> tuple[str, bool, str]:
    if "=" not in word:
        return word, False, ""
    flag, value = word.split("=", 1)
    return flag, True, value


def _short_flag_with_attached_value(word: str, safe_flags: dict[str, str]) -> tuple[str, str, str] | None:
    if not word.startswith("-") or word.startswith("--") or len(word) <= 2:
        return None
    flag = word[:2]
    kind = safe_flags.get(flag)
    if not kind or kind == NONE:
        return None
    return flag, kind, word[2:]


def _short_flag_cluster_allowed(flag: str, safe_flags: dict[str, str]) -> bool:
    if not flag.startswith("-") or flag.startswith("--") or len(flag) <= 2:
        return False
    return all(safe_flags.get(f"-{name}") == NONE for name in flag[1:])


def _option_like_string_allowed(value: str, command_name: str, flag: str) -> bool:
    if not value.startswith("-") or len(value) <= 1 or OPTION_PATTERN.match(value) is None:
        return True
    return command_name == "git" and flag == "--sort" and re.match(r"^-[a-zA-Z]", value) is not None


def _matches_flag_value_kind(value: str, kind: str) -> bool:
    if kind == NONE:
        return False
    if kind == NUMBER:
        return value.isdigit()
    if kind in {STRING, OPTIONAL_STRING}:
        return True
    if kind == CHAR:
        return len(value) == 1
    if kind == BRACE:
        return value == "{}"
    if kind == EOF:
        return value == "EOF"
    return False


def _argv_allowed_by_policy(
    argv: list[str],
    policy: CommandPolicy,
    command_name: str,
    start: int = 1,
) -> bool:
    if not argv:
        return False
    if policy.allow_any_args:
        return True
    if policy.command_only:
        return len(argv) == start
    if not policy.safe_flags:
        return False

    safe_flags = policy.safe_flags
    index = start
    while index < len(argv):
        word = argv[index]
        if not word:
            index += 1
            continue
        if command_name == "xargs" and (not word.startswith("-") or word == "--"):
            target = argv[index + 1] if word == "--" and index + 1 < len(argv) else word
            if not target or target not in XARGS_TARGET_COMMANDS:
                return False
            break
        if word == "--":
            if policy.respects_double_dash is False:
                index += 1
                continue
            break
        if command_name in {"head", "tail"} and re.fullmatch(r"-\d+", word):
            index += 1
            continue
        if policy.allow_compact_numeric and re.fullmatch(r"-\d+", word):
            index += 1
            continue
        if len(word) > 1 and word.startswith("-") and OPTION_PATTERN.match(word):
            flag, has_inline, inline = _parse_option_word(word)
            kind = safe_flags.get(flag)
            if not kind:
                attached = _short_flag_with_attached_value(word, safe_flags)
                if attached:
                    attached_flag, attached_kind, attached_value = attached
                    if not _option_like_string_allowed(attached_value, command_name, attached_flag):
                        return False
                    if not _matches_flag_value_kind(attached_value, attached_kind):
                        return False
                    index += 1
                    continue
                if _short_flag_cluster_allowed(flag, safe_flags):
                    index += 1
                    continue
                return False
            if kind == NONE:
                if has_inline:
                    return False
                index += 1
                continue
            if kind == OPTIONAL_STRING:
                index += 1
                continue
            value = inline if has_inline else (argv[index + 1] if index + 1 < len(argv) else None)
            if value is None:
                return False
            if kind == STRING and not has_inline and not _option_like_string_allowed(
                value, command_name, flag
            ):
                return False
            if not _matches_flag_value_kind(value, kind):
                return False
            index += 1 if has_inline else 2
            continue
        index += 1
    return True


def _find_argv_safe(argv: list[str]) -> bool:
    index = 1
    while index < len(argv):
        word = argv[index]
        if word in _FIND_WRITE_OPTIONS:
            return False
        if word in _FIND_VALUE_OPTIONS or _FIND_NEWER_PATTERN.match(word):
            index += 2
            continue
        index += 1
    return True


def _printf_argv_safe(argv: list[str]) -> bool:
    if len(argv) > 1 and argv[1].startswith("-") and argv[1] != "--":
        return False
    format_index = 2 if len(argv) > 1 and argv[1] == "--" else 1
    format_text = argv[format_index] if format_index < len(argv) else ""
    if "$" in format_text:
        return False
    normalized = format_text.replace("%%", "")
    if re.search(r"%[^%a-zA-Z]*(?:hh|ll|[lLhqjzZt])?\\[0-7xX]", normalized):
        return False
    if re.search(r"\\[uU]", normalized):
        return False
    numeric = re.search(
        r"%[-+ 0#']*[0-9.*]*(?:hh|ll|[lLhqjzZt])?[diouxXeEfFgGaAn]", normalized
    )
    if numeric or re.search(r"%[^%a-zA-Z]*\*", normalized):
        for value in argv[format_index + 1 :]:
            if "[" in value or "`" in value or "$(" in value:
                return False
            if re.fullmatch(
                r"[-+]?(0[xX][0-9a-fA-F]+|[0-9]+#[0-9a-zA-Z]+|[0-9]*\.?[0-9]+([eE][-+]?[0-9]+)?)",
                value,
            ) is None:
                return False
    return True


def _direct_argv_result(argv: list[str]) -> bool | None:
    if any(len(argv) == len(expected) and list(argv) == list(expected) for expected in _EXACT_ARGV_COMMANDS):
        return True
    if argv and argv[0] == "docker" and any(
        argv[: len(prefix)] == list(prefix) for prefix in READONLY_ALLOW_ANY_ARG_COMMAND_PREFIXES
    ):
        return not has_dangerous_docker_option(argv)
    if argv and argv[0] == "printf":
        return _printf_argv_safe(argv)
    if argv and argv[0] == "find":
        return _find_argv_safe(argv)
    if argv and argv[0] == "history":
        return len(argv) == 1 or (len(argv) == 2 and argv[1].isdigit())
    if argv and argv[0] == "arch":
        return len(argv) == 1 or (len(argv) == 2 and argv[1] in {"-h", "--help"})
    if argv and argv[0] == "ifconfig":
        return len(argv) == 1 or (
            len(argv) == 2 and re.match(r"^[a-zA-Z]", argv[1]) is not None
        )
    return None


def _git_normalize(argv: list[str]) -> list[str] | None:
    normalized = ["git"]
    index = 1
    while index < len(argv):
        word = argv[index]
        if not word:
            index += 1
            continue
        if word in GIT_GLOBAL_NO_VALUE_FLAGS:
            index += 1
            continue
        if _git_dangerous_global_word(word):
            return None
        if word in GIT_GLOBAL_VALUE_FLAGS:
            index += 2
            if index - 1 >= len(argv):
                return None
            continue
        if word.startswith("-"):
            return None
        normalized.extend(argv[index:])
        return normalized
    return None


def _git_dangerous_global_word(word: str) -> bool:
    for flag in GIT_ATTACHED_DANGEROUS_SHORT_FLAGS:
        if len(word) > len(flag) and word.startswith(flag) and (flag == "-C" or word[len(flag)] != "-"):
            return True
    if word in GIT_GLOBAL_DANGEROUS_FLAGS:
        return True
    return any(word.startswith(f"{flag}=") for flag in GIT_GLOBAL_DANGEROUS_FLAGS)


def _git_read_only(argv: list[str]) -> bool:
    normalized = _git_normalize(argv)
    if not normalized:
        return False
    for prefix in sorted(GIT_READONLY_POLICIES, key=lambda item: len(item.split()), reverse=True):
        words = prefix.split()
        if normalized[: len(words)] != words:
            continue
        policy = GIT_READONLY_POLICIES[prefix]
        args = normalized[len(words) :]
        if policy.callback and policy.callback(args):
            return False
        return _argv_allowed_by_policy(normalized, policy, "git", len(words))
    return False


def _multiword_result(argv: list[str]) -> bool | None:
    for prefix in sorted(MULTIWORD_POLICIES, key=lambda item: len(item.split()), reverse=True):
        words = prefix.split()
        if argv[: len(words)] != words:
            continue
        policy = MULTIWORD_POLICIES[prefix]
        args = argv[len(words) :]
        if policy.callback and policy.callback(args):
            return False
        return _argv_allowed_by_policy(argv, policy, argv[0], len(words))
    return None


def _evaluate_invocation(invocation: Invocation) -> bool:
    if not _env_assignments_allowed(invocation) or not _redirects_allowed(invocation):
        return False
    argv = _strip_safe_wrappers(invocation.argv)
    if not argv:
        return False
    if any(_is_unsafe_unc(word) for word in argv):
        return False
    if argv[0] == "git":
        return _git_read_only(argv)

    direct = _direct_argv_result(argv)
    if direct is not None:
        return direct

    multiword = _multiword_result(argv)
    if multiword is not None:
        return multiword

    if argv[0] in READONLY_ALLOW_ANY_ARG_COMMANDS:
        return True
    if sys.platform == "win32" and argv[0] == "xargs":
        return False

    policy = COMMAND_POLICIES.get(argv[0])
    if policy is None:
        return False
    if argv[0] == "cd" and len(argv) > 2:
        return False
    if policy.callback and policy.callback(argv[1:]):
        return False
    if policy.regex and re.fullmatch(policy.regex, " ".join(argv)) is None:
        return False
    return _argv_allowed_by_policy(argv, policy, argv[0])


def is_read_only_analysis(analysis: Analysis) -> bool:
    """Return True when every invocation in ``analysis`` is provably read-only."""
    if not analysis.permission_safe or not analysis.commands:
        return False
    names = {invocation.name for invocation in analysis.commands}
    if "git" in names and "cd" in names:
        return False
    has_read_only = False
    for invocation in analysis.commands:
        if not _evaluate_invocation(invocation):
            return False
        has_read_only = True
    return has_read_only


def is_read_only_command(command: str, *, shell: str = "") -> bool:
    """Classify ``command`` as read-only for the given shell.

    Non-POSIX shells (PowerShell, cmd) never qualify: their syntax cannot be
    checked by this analyzer, so the caller keeps normal approval behavior.
    """
    if str(shell or "").strip().lower() not in READ_ONLY_SHELLS:
        return False
    return is_read_only_analysis(analyze(command))

