import subprocess
import sys
import time
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
REPO_URL = "https://github.com/GivlerHP/Python-git-update.git"
BRANCH = "master"
PROGRAM = PROJECT_DIR / "screen.pyw"
CHECK_INTERVAL = 60  # проверять обновления раз в 60 секунд


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
        return

    remotes = git("remote", check=False).stdout.split()
    if "origin" not in remotes:
        git("remote", "add", "origin", REPO_URL)


def update_available():
    fetched = git("fetch", "origin", BRANCH, check=False)
    if fetched.returncode != 0:
        print("Не удалось проверить обновления:", fetched.stderr.strip())
        return False

    local = git("rev-parse", "HEAD", check=False).stdout.strip()
    remote = git("rev-parse", f"origin/{BRANCH}", check=False).stdout.strip()
    return bool(remote) and local != remote


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


def main():
    try:
        prepare_repository()
    except (OSError, RuntimeError) as error:
        print(f"Ошибка подготовки Git: {error}")
        input("Нажмите Enter для выхода...")
        return

    process = start_program()

    try:
        while True:
            time.sleep(CHECK_INTERVAL)

            if not update_available():
                continue

            print("Найдено обновление. Перезапускаю программу...")
            process.terminate()
            process.wait()

            if update_repository():
                process = start_program()
            else:
                process = start_program()

    except KeyboardInterrupt:
        process.terminate()
        process.wait()


if __name__ == "__main__":
    main()
