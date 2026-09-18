import subprocess
import sys
import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
REPO_URL = "https://github.com/GivlerHP/Python-git-update.git"
BRANCH = "master"
PROGRAM = PROJECT_DIR / "screen.pyw"
def hide_console():
    """Перезапускает обновлятор без консольного окна Windows."""
    if os.name != "nt" or os.environ.get("UPDATER_BACKGROUND") == "1":
        return

    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        return

    environment = os.environ.copy()
    environment["UPDATER_BACKGROUND"] = "1"
    subprocess.Popen(
        [str(pythonw), str(Path(__file__).resolve())],
        cwd=PROJECT_DIR,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    sys.exit(0)


def git(*args, check=True):
    result = subprocess.run(
        ["git", *args],
        cwd=PROJECT_DIR,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Команда Git завершилась с ошибкой")
    return result


def prepare_repository():
    git_dir = PROJECT_DIR / ".git"

    if not git_dir.exists():
        print("Папка .git не найдена. Подключаю GitHub-репозиторий...")
        git("init")
        git("remote", "add", "origin", REPO_URL)
        # Добавляем текущие файлы в индекс, чтобы checkout мог заменить их
        # содержимым репозитория даже при первом запуске.
        git("add", ".")
        git(
            "-c", "user.name=Auto Updater",
            "-c", "user.email=updater@localhost",
            "commit", "-m", "Initial local snapshot",
            check=False,
        )
        git("fetch", "origin", BRANCH)

        # Синхронизируем локальные файлы с репозиторием.
        # Папка files не затрагивается, потому что она добавлена в .gitignore.
        git("checkout", "-B", BRANCH, f"origin/{BRANCH}")
        print("Репозиторий подключён.")
        return True

    remotes = git("remote", check=False).stdout.split()
    if "origin" not in remotes:
        git("remote", "add", "origin", REPO_URL)

    return False


def update_available():
    fetched = git("fetch", "origin", BRANCH, check=False)
    if fetched.returncode != 0:
        print("Не удалось проверить обновления:", fetched.stderr.strip())
        return False

    local = git("rev-parse", "HEAD", check=False).stdout.strip()
    remote = git("rev-parse", f"origin/{BRANCH}", check=False).stdout.strip()
    return bool(remote) and local != remote


def restore_missing_files():
    """Восстанавливает удалённые отслеживаемые файлы текущего коммита."""
    result = git("diff", "--name-only", "--diff-filter=D", check=False)
    missing = [name for name in result.stdout.splitlines() if name]
    if not missing:
        return False

    restored = git("restore", "--source=HEAD", "--worktree", "--", *missing, check=False)
    if restored.returncode != 0:
        print("Не удалось восстановить удалённые файлы:", restored.stderr.strip())
        return False

    print(f"Восстановлено файлов: {len(missing)}")
    return "launcher.py" in {Path(name).as_posix() for name in missing}


def update_repository():
    result = git("pull", "--ff-only", "origin", BRANCH, check=False)
    if result.returncode != 0:
        print("Не удалось установить обновление:", result.stderr.strip())
        return False

    print("Обновление установлено.")
    return True


def start_program():
    return subprocess.Popen(
        [sys.executable, str(PROGRAM)],
        cwd=PROJECT_DIR,
    )


def restart_launcher():
    """Запускает уже обновлённую копию launcher.py и завершает старую."""
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=PROJECT_DIR,
        env=os.environ.copy(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    sys.exit(0)


def main():
    hide_console()

    try:
        launcher_replaced = prepare_repository()
    except (OSError, RuntimeError) as error:
        print(f"Ошибка подготовки Git: {error}")
        input("Нажмите Enter для выхода...")
        return

    if launcher_replaced:
        restart_launcher()

    launcher_restored = restore_missing_files()

    if update_available():
        print("Найдено обновление. Устанавливаю его перед запуском...")
        if update_repository():
            restart_launcher()

    if launcher_restored:
        restart_launcher()

    # Основная программа работает самостоятельно, а обновлятор завершается.
    start_program()


if __name__ == "__main__":
    main()
