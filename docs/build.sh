#!/bin/bash
set -e

SCRIPT_DIR=$(dirname "$0")
VENV_DIR="${SCRIPT_DIR}/.venv"
BUILD_DIR=build_docs
[[ -z "${DOCS_OUTPUT_DIR}" ]] && DOCS_OUTPUT_DIR=generated/docs
[[ -z "${GENERATED_RST_DIR}" ]] && GENERATED_RST_DIR=generated/rst
[[ -z "${GENERATED_AUTOGEN_RST_DIR}" ]] && GENERATED_AUTOGEN_RST_DIR=generated/rst/autogen

# Bootstrap an isolated virtualenv for docs so Sphinx and its extensions
# do not pollute the main project's uv environment.
if [[ ! -x "${VENV_DIR}/bin/sphinx-build" ]]; then
    python3 -m venv "${VENV_DIR}"
fi
# Pinned: the pip a fresh venv bundles may predate --uploaded-prior-to, and
# this install runs before the cooldown below can apply.
"${VENV_DIR}/bin/pip" install --quiet "pip==26.2.2"
# Resolve only distributions uploaded at least a week ago, matching the uv and
# bun cooldown, so a compromised release has time to be noticed and yanked.
"${VENV_DIR}/bin/pip" install --quiet --uploaded-prior-to P7D -r "${SCRIPT_DIR}/requirements.txt"

rm -rf "${DOCS_OUTPUT_DIR}"
mkdir -p "${DOCS_OUTPUT_DIR}"

rm -rf "${GENERATED_RST_DIR}"
mkdir -p "${GENERATED_RST_DIR}"

rsync -aLv "${SCRIPT_DIR}"/root/ "${SCRIPT_DIR}"/conf.py "${GENERATED_RST_DIR}"

export EXIT_ON_BAD_CONFIG='false'
set -x
"${VENV_DIR}/bin/sphinx-autogen" -o "${GENERATED_RST_DIR}" "${GENERATED_RST_DIR}"/*.rst
"${VENV_DIR}/bin/sphinx-build" -j auto --keep-going -b html "${GENERATED_RST_DIR}" "${DOCS_OUTPUT_DIR}"
