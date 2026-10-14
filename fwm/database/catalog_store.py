from fwm.data_loader.file_catalog import FileCatalog
from fwm.database.schema import TreeCatalog, CatalogFile, CatalogLink, CatalogDir

# Bound variables per INSERT: the limit of SQLite before 3.32 (e.g. RHEL 8's 3.26), which newer ones also accept
_MAX_VARIABLES = 999


def save_catalog(catalog: FileCatalog) -> TreeCatalog:
    """
    Save a file catalog into the database. Call it inside a transaction.

    :param catalog: The catalog to save.
    :return: The saved catalog.
    """
    tree = TreeCatalog.create(digest_algo=catalog.digest_algo, creation_time=catalog.creation_time)

    def insert(model, rows):
        if not rows:
            return
        batch = _MAX_VARIABLES // len(rows[0])
        for i in range(0, len(rows), batch):
            model.insert_many(rows[i:i + batch]).execute()

    insert(CatalogFile, [
        {"catalog": tree, "path": f.path, "perm": f.perm, "actual_size": f.actual_size, "digest": f.digest}
        for f in catalog.files
    ])
    insert(CatalogLink, [
        {"catalog": tree, "path": link.path, "perm": link.perm, "dest": link.dest}
        for link in catalog.links
    ])
    insert(CatalogDir, [
        {"catalog": tree, "path": d.path, "perm": d.perm}
        for d in catalog.dirs
    ])
    return tree


def load_catalog(tree: TreeCatalog) -> FileCatalog:
    """
    Load a file catalog from the database.

    :param tree: The saved catalog.
    :return: The catalog, in its in-memory form.
    """
    return FileCatalog(
        creation_time=tree.creation_time,
        digest_algo=tree.digest_algo,
        files=[
            {"path": f.path, "perm": f.perm, "actual_size": f.actual_size, "digest": f.digest}  # type: ignore
            for f in CatalogFile.select().where(CatalogFile.catalog == tree).order_by(CatalogFile.path)
        ],
        links=[
            {"path": link.path, "perm": link.perm, "dest": link.dest}  # type: ignore
            for link in CatalogLink.select().where(CatalogLink.catalog == tree).order_by(CatalogLink.path)
        ],
        dirs=[
            {"path": d.path, "perm": d.perm}  # type: ignore
            for d in CatalogDir.select().where(CatalogDir.catalog == tree).order_by(CatalogDir.path)
        ],
    )


def delete_catalog(tree: TreeCatalog) -> None:
    """
    Delete a file catalog from the database, with all its items. Call it inside a transaction, after deleting the rows
    that refer to it.
    """
    tree.delete_instance()
