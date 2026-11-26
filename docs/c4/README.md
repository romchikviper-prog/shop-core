# C4-діаграми як код

## Файли

| Файл | Рівень | Призначення |
|---|---|---|
| `context.puml` | C4-1 Context | Система, її користувачі та зовнішні системи |
| `container.puml` | C4-2 Container | Web-магазин, Shop Application, PostgreSQL і зовнішні сервіси |
| `context.png`, `container.png` | — | Згенеровані зображення для README і звіту |
| `lib/` | — | Локальна копія C4-PlantUML (MIT), щоб рендер не залежав від мережі |

Рівень 3 (Component) у ЛР1 не будується: завдання вимагає рівні 1–2, а компонентний рівень з'явиться в наступних роботах.

## Рендер

Потрібен Java 17+ і `plantuml.jar`. З кореня `docs/c4`:

```bash
java -jar plantuml.jar -DRELATIVE_INCLUDE=lib -tpng context.puml container.puml
```

`-DRELATIVE_INCLUDE=lib` — це опція **PlantUML** (після `-jar`), а не Java. Вона змушує `C4_Context.puml` включити локальний `lib/C4.puml`, а не завантажувати його з GitHub. PlantUML використовує вбудований рушій Smetana, тому Graphviz не потрібен.

## Підписи

- Кожен елемент має призначення; контейнери додатково мають технологію (`Python 3.13, FastAPI`, `PostgreSQL 16`, `TypeScript, React, SPA`).
- Кожен зв'язок має спосіб взаємодії: `HTTPS/JSON`, `HTTPS/REST`, `SQL/TLS`, `HTTPS`.
- Зовнішні системи позначені сірим кольором, система в межах — синім, люди — темно-синім.

## Ліцензія та сумісність

`lib/C4.puml`, `lib/C4_Context.puml`, `lib/C4_Container.puml` — C4-PlantUML 2.15.0beta1, ліцензія MIT (див. `lib/LICENSE`). У `C4.puml` локальної копії префіксовано службові змінні `NEW_C4_STYLE`, `ROUNDED_STYLE`, `NO_LAY_ROTATE` символом `$`: цього вимагає PlantUML 1.2026.10, інакше він друкує попередження про застарілий синтаксис. На зміст діаграм це не впливає.
