"""Вендорені шрифти + перевірка їх SHA-256 на старті.

НАВІЩО. Верстка наліпки — це не «текст на канві», а метрики конкретного білда
шрифту: wrap() міряє ширину рядка через ImageDraw.textlength, text_fit()
ступінчасто зменшує кегль по тій самій метриці. Інший білд DejaVu → інші точки
переносу → інший склад/алергени на юридично значущій наліпці, яка при цьому
виглядає правдоподібно.

Раніше шрифти приходили з `apt install fonts-dejavu-core`, а apt — не система
версіонування: наступна перезбірка образу могла мовчки підсунути інший файл.
Тепер обидва ttf лежать у репозиторії (labels/fonts/), а їх хеші зашиті нижче
в КОДІ (не в файлі поруч — інакше підміна пари «шрифт + список хешів» пройшла б
непоміченою). Розбіжність = застосунок не стартує.

Хеші знято з бойового образу freezer-labels-labels:latest, тобто саме з тих
файлів, якими зроблено 66 еталонних наліпок (migrate/output/golden-hashes.json).
"""
import hashlib
import os

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# ім'я файлу -> очікуваний SHA-256 (DejaVu 2.37, Debian bookworm, 2023-08-10)
EXPECTED_FONT_SHA256 = {
    "DejaVuSans.ttf":
        "57f73e11f51999432bf7ab22ce55b6f945d5eca1bf824404cfa9ec2e3718c84e",
    "DejaVuSans-Bold.ttf":
        "a4c5bc453ca281d90ea079e596da7ae0dfeb5777497c29ec254e76d97ff6f890",
}


class FontIntegrityError(RuntimeError):
    """Шрифт відсутній або має інший вміст — рендерити наліпки заборонено."""


def font_path(filename):
    return os.path.join(FONT_DIR, filename)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def actual_font_hashes():
    """{ім'я файлу: sha256 | None, якщо файла нема} — для /api/healthz."""
    out = {}
    for name in EXPECTED_FONT_SHA256:
        path = font_path(name)
        out[name] = sha256_file(path) if os.path.exists(path) else None
    return out


def verify_fonts():
    """Кидає FontIntegrityError, якщо будь-який шрифт зник або змінився."""
    problems = []
    for name, expected in EXPECTED_FONT_SHA256.items():
        path = font_path(name)
        if not os.path.exists(path):
            problems.append(f"{name}: файла нема ({path})")
            continue
        got = sha256_file(path)
        if got != expected:
            problems.append(f"{name}: SHA-256 {got} != очікуваного {expected}")
    if problems:
        raise FontIntegrityError(
            "ПЕРЕВІРКА ШРИФТІВ НЕ ПРОЙДЕНА — сервіс не стартує.\n"
            "Наліпка юридично значуща, а її верстка залежить від метрик саме цих\n"
            "файлів. Не «полагодь хеш», а поверни правильний шрифт:\n  - "
            + "\n  - ".join(problems)
        )
    return True
