"""Cartography sync worker package.

Shared by the Seizu reporting app (config validation, workflow input) and the
dedicated cartography sync image's thin Temporal worker. The sync image ships
only this package, so nothing here may import ``reporting.*`` (which pulls in
pydantic settings); ``registry`` and ``shared`` must stay stdlib-only, and
``activities``/``worker``/``sync_lock`` may additionally use ``temporalio``
and ``neo4j`` (both pip-installed into the image by Dockerfile.cartography).
"""
