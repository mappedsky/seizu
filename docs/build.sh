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
    uv venv --quiet --python 3.12 "${VENV_DIR}"
fi
# Resolve only distributions uploaded at least a week ago, matching the
# exclude-newer window in pyproject.toml, so a compromised release has time to
# be noticed and yanked.
uv pip install --quiet --python "${VENV_DIR}/bin/python" \
    --exclude-newer "7 days" -r "${SCRIPT_DIR}/requirements.txt"

rm -rf "${DOCS_OUTPUT_DIR}"
mkdir -p "${DOCS_OUTPUT_DIR}"

rm -rf "${GENERATED_RST_DIR}"
mkdir -p "${GENERATED_RST_DIR}"

rsync -aLv "${SCRIPT_DIR}"/root/ "${SCRIPT_DIR}"/conf.py "${GENERATED_RST_DIR}"

export EXIT_ON_BAD_CONFIG='false'
set -x
"${VENV_DIR}/bin/sphinx-autogen" -o "${GENERATED_RST_DIR}" "${GENERATED_RST_DIR}"/*.rst
"${VENV_DIR}/bin/sphinx-build" -j auto --keep-going -b html "${GENERATED_RST_DIR}" "${DOCS_OUTPUT_DIR}"
