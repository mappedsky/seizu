"""Developer scripts, importable as ``scripts.<name>``.

A package rather than loose files so ``python -m scripts.chat_harness`` and a
test importing ``scripts.chat_harness`` resolve the module by the same name --
mypy rejects a source file it can reach under two names.
"""
