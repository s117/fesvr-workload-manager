def normalize_target_path(pwd: str, path: str) -> str:
    """
    Normalize a target path the way FESVR does (target_cwd::normalize_path in riscv-fesvr/fesvr/target_cwd.cc).

    A relative path is resolved against pwd. Empty and "." components are dropped, and ".." removes the previous
    component (".." at the root stays at the root). This is purely lexical: no symlink is followed.

    :param pwd: The target's working directory. It goes through the same rules, so it doesn't need to be normalized.
    :param path: The target path to normalize.
    :return: The normalized absolute target path.
    """
    components: list[str] = []

    def push_components(p: str) -> None:
        for component in p.split("/"):
            if component == "..":
                if components:
                    components.pop()
            elif component and component != ".":
                components.append(component)

    if not path.startswith("/"):
        push_components(pwd)
    push_components(path)

    return "/" + "/".join(components)


def target_path_to_sysroot_relative(target_path: str) -> str:
    """
    Convert a normalized absolute target path to a path relative to the sysroot, as used in a file catalog.

    :param target_path: A normalized absolute target path, e.g. "/app/input.txt".
    :return: The path relative to the sysroot, e.g. "app/input.txt", or "." for the root.
    """
    assert target_path.startswith("/"), target_path
    relative = target_path.lstrip("/")
    return relative if relative else "."
