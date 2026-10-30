# Blender Bridge

Открыть любую модель или клип Unity в Blender одной кнопкой и отправить обратно другой. FBX остаётся источником истины: GUID, .meta, ссылки префабов и настройки импорта сохраняются.

- Unity 6000.0+, Blender 5.0+ (рекомендуется 5.2 LTS), macOS / Windows / Linux.
- Установка: Package Manager → Add package from git URL → `https://github.com/<ты>/blender-bridge.git#v0.1.0`.
- Аддон для Blender ставить не нужно — он загружается из пакета.

Полное руководство: [Documentation~/index.md](Documentation~/index.md).

## Состав

| Папка | Что внутри |
| --- | --- |
| `Editor/` | Unity: меню, кнопка в инспекторе, запуск Blender, импорт результата, проверки, окно, настройки |
| `Blender~/` | Аддон Blender (`unity_bridge`) и `bootstrap.py`; Unity эту папку не импортирует |
| `Tests/Editor/` | Тесты Unity (Edit Mode) |
| `Tests~/blender/` | Стенд этапа 0: циклы FBX → Blender → FBX и сравнение «как видит Unity» |
| `Documentation~/` | Документация на русском |
