# vkqr

CLI-утилита: получает cookies сессии **ВКонтакте (vk.ru)** через вход по **QR-коду VK ID** и сохраняет их в JSON.

QR-код выводится прямо в терминал. Готовый скрипт сам ставит Python и pipx вместе с необходимыми Python-пакетами.

## Как это работает

1. Утилита запрашивает у VK ссылку для входа, печатает её в **stdout** и показывает соответствующий QR-код в терминале.
2. Вы сканируете QR-код приложением ВКонтакте. Не открывайте ссылку из stdout в браузере: это адрес для сканирования, а не страница с QR-кодом.
3. Телефон показывает код — утилита читает его из **stdin**.
4. Утилита получает cookies (`p`, `remixsid`, ...) и дописывает запись в JSON-файл.

## Требования

- Python 3.9+.

## Установка и запуск

Один самодостаточный скрипт на платформу: сам ставит Python (если его нет), git, pipx и запускает утилиту.

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/matrixd0t/vkqr/master/scripts/vkqr.sh -o vkqr.sh
sh vkqr.sh -o cookies.json
```

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/matrixd0t/vkqr/master/scripts/vkqr.ps1 -OutFile vkqr.ps1
powershell -ExecutionPolicy Bypass -File .\vkqr.ps1 -o cookies.json
```

> Не запускайте скрипт через `... | sh` или `... | iex`: тогда stdin занят текстом скрипта, и ввести код с телефона уже не получится. Скачивайте скрипт в файл (как выше).

### Если Python и pipx уже установлены

```bash
pipx run --spec "git+https://github.com/matrixd0t/vkqr.git" vkqr -o cookies.json
```

### Установить как команду

```bash
pipx install "git+https://github.com/matrixd0t/vkqr.git"
vkqr -o cookies.json
```

## Пример использования

```console
$ vkqr -o cookies.json
[QR-код отображается в терминале]
Введите код с экрана телефона: 482134
OK: user_id=580106999, cookies сохранены в /home/user/cookies.json
```

- Ссылка для QR печатается в **stdout**; сам QR-код, подсказки и ошибки выводятся в **stderr**.

Если нужно показать QR на другом устройстве, можно сохранить картинку командой `qrencode -o qr.png "<ссылка из stdout>"`.

## Флаг `-o`

```bash
vkqr -o cookies.json
```

- Если `-o` задан — запись сохраняется в указанный файл.
- Если `-o` не задан — путь запрашивается интерактивно **после** успешного входа (тоже через stdin).

Запуск напрямую из исходников (без установки) — `python3 src/vkqr.py -o cookies.json`.

## Формат JSON

```json
{
  "cookies": [
    {
      "created_at": 1700000000,
      "p": "....",
      "remixsid": "...."
    }
  ]
}
```

Правила:

- Файла нет — он создаётся с массивом `cookies` и одной записью.
- Файл есть — новая запись **добавляется** в конец массива `cookies` (файл не перезаписывается).
- Запись: `created_at` (unix seconds), `p`, `remixsid`; `user_id` (int) добавляется, если VK его возвращает.
- Для сохранения сессии обязательны только `p` и `remixsid`.
- Если структура файла не позволяет дополнить список (невалидный JSON, корень не объект, `cookies` не список) — выводится ошибка, файл не изменяется.

Так можно хранить cookies нескольких аккаунтов в одном файле на одном сервере. На разных серверах удобнее запускать утилиту локально:

```bash
ssh server1 'curl -fsSL https://raw.githubusercontent.com/matrixd0t/vkqr/master/scripts/vkqr.sh -o /tmp/vkqr.sh && sh /tmp/vkqr.sh -o cookies.json'
ssh server2 'curl -fsSL https://raw.githubusercontent.com/matrixd0t/vkqr/master/scripts/vkqr.sh -o /tmp/vkqr.sh && sh /tmp/vkqr.sh -o cookies.json'
```

## Безопасность

`p` и `remixsid` дают полный доступ к аккаунту ВКонтакте. Не коммитьте файл с cookies, храните его с правами `chmod 600` и не передавайте третьим лицам.
