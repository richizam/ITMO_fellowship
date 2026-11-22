# Fellowship Candidate Intelligence

AI-инструмент программы **ITMO Fellowship** для подготовки аналитического досье кандидата.
MVP из двух блоков:

1. **Досье кандидата** — CV, research proposal, анкета заявки и идентификаторы (ORCID, Scopus Author ID,
   Google Scholar) превращаются в структурированную карточку за несколько секунд.
2. **Подбор подразделений ИТМО** — профиль кандидата сопоставляется с научной картой ИТМО (официальные страницы
   факультетов, институтов, центров и лабораторий), система предлагает наиболее релевантные подразделения
   с обоснованием и направлениями сотрудничества.

## Что входит в досье

| Блок | Содержимое | Источник |
|---|---|---|
| Базовые сведения | ФИО, трек, дата рождения и возраст, гражданство, место работы, PhD, подразделение и контакт в ИТМО, рекомендатели | анкета Tilda (`Заявка.docx`), CV |
| Карточка | Те же строки, что в таблице команды «Кандидаты.xlsx» (экспорт в том же формате) | — |
| Публикации за 4 года + текущий | год, тип, журнал, ISSN, DOI, позиция автора (первый / последний / corresponding), цитирования | OpenAlex, ORCID, Google Scholar |
| Качество журналов | квартиль, источник (SJR), год квартиля, все предметные категории с квартилями | SCImago Journal Rank |
| Наукометрия | H-index Scopus / Google Scholar / OpenAlex, i10, цитирования, «с 2021», число публикаций по годам, перерывы | Scopus API, Google Scholar, OpenAlex |
| Проверки | сверка H-index из CV с внешними данными, некорректные ссылки, расхождение имён, перерывы, Q1/Q2 первым автором | все источники |
| Исследовательский профиль | резюме, интересы, методы, компетенции, прикладные направления, ключевые слова, тема и суть проекта, заметки для эксперта | LLM (DeepSeek / Claude) или эвристика |
| Подразделения ИТМО | топ-6 лабораторий / центров / факультетов, релевантность, почему (совпавшие публикации и интересы, общие термины), направления сотрудничества, место подразделения из заявки в рейтинге | научная карта ИТМО + многоязычные эмбеддинги + TF-IDF (+ LLM) |

## Быстрый старт

Нужен Python ≥ 3.11 и [uv](https://docs.astral.sh/uv/) (`pip install uv`). Для `.doc` — LibreOffice.

```bash
uv sync                                     # зависимости
uv run python scripts/build_sjr.py          # база квартилей SJR -> data/sjr.sqlite (~50 МБ, один раз)
uv run python scripts/fetch_examples.py <ссылка на папку Drive>   # примеры заявок -> data/examples/
uv run python scripts/build_examples.py     # досье по всем примерам (≈1 мин, ответы кэшируются)
uv run python scripts/eval_matching.py      # качество подбора подразделений на примерах
uv run uvicorn app.main:app --port 8000     # http://127.0.0.1:8000
```

Ключи (необязательно) — в `.env`, см. `.env.example`:

* `DEEPSEEK_API_KEY` (модель `deepseek-flash`) или `ANTHROPIC_API_KEY` (Claude) — LLM пишет исследовательский профиль
  и переранжирует подразделения ИТМО с обоснованием. Подходит любой OpenAI-совместимый endpoint (`LLM_API_KEY`,
  `LLM_BASE_URL`, `LLM_MODEL`). Без ключа профиль собирается эвристически, подбор — только семантическим поиском.
* `SCOPUS_API_KEY` (+ `SCOPUS_INST_TOKEN`) — H-index Scopus через Author Retrieval API; работает из сети ИТМО. Без ключа значение можно ввести вручную в форме.
* `OPENALEX_API_KEY` — собственный дневной лимит OpenAlex (без ключа общий бесплатный лимит).

Тесты: `uv run pytest`.

## Развёртывание на сервере (Docker)

```bash
git clone git@github.com:richizam/ITMO_fellowship.git && cd ITMO_fellowship
cp .env.example .env        # задать APP_USERNAME / APP_PASSWORD, SITE_ADDRESS и ключи API
mkdir -p data/examples      # скопировать сюда папки с примерами заявок (scp / fetch_examples.py)
docker compose up -d --build                    # только приложение, порт из APP_PUBLISH
# или с собственным HTTPS-прокси (если порты 80/443 свободны):
docker compose --profile https up -d --build
docker compose exec app python scripts/build_examples.py   # досье по примерам
```

* Образ сам собирает базу квартилей SJR, скачивает модель эмбеддингов и строит индекс научной карты; данные
  (досье, загрузки, кэш) лежат в `./data` на сервере.
* `APP_PUBLISH` — где опубликовано приложение: `127.0.0.1:8000` (за своим nginx) или `0.0.0.0:8790` (напрямую).
* Профиль `https` запускает Caddy на 80/443: `SITE_ADDRESS=:80` — HTTP по IP; домен или `1-2-3-4.sslip.io` — автоматический HTTPS.
* Сайт закрыт логином/паролем, если заданы `APP_USERNAME` и `APP_PASSWORD`.

## Как это устроено

```
app/
  main.py            FastAPI: страницы, загрузка документов, фоновые задачи, экспорт
  pipeline.py        оркестратор: документы → идентификаторы → источники → публикации → метрики → профиль
  ingest/            извлечение текста (PDF/DOCX/DOC), разбор анкеты Tilda, ORCID/Scopus/Scholar и метрики из CV
  sources/           Google Scholar, ORCID, OpenAlex, Scopus API, SJR + HTTP-клиент с кэшем
  analysis/          сопоставление имён (лат./кир.), слияние публикаций, метрики и проверки, профиль (Claude),
                     matching.py — этап 2, подбор подразделений ИТМО
  data/itmo_units.json  научная карта ИТМО (scripts/build_itmo_map.py)
  export.py          Excel: досье и сводка в формате «Кандидаты.xlsx»
  templates/, static/  интерфейс в стиле fellowship.itmo.ru
scripts/             build_sjr.py, build_itmo_map.py, fetch_examples.py, build_examples.py, eval_matching.py
tests/               офлайн-тесты парсеров и логики
```

Ключевые решения:

* **Идентификаторы**: введено вручную → анкета → CV. Источник каждого значения показывается в досье.
* **OpenAlex** — основной источник метаданных (DOI, полный список авторов → позиция кандидата, ISSN → квартиль).
  Автор находится по ORCID; разделённые профили OpenAlex добавляются, только если их работы совпадают с Google Scholar.
* **Google Scholar** не имеет API: читается публичная страница профиля (одна страница на 100 работ, пауза между запросами, кэш).
  При CAPTCHA досье строится без Scholar, это видно в блоке «Источники».
* **Квартили**: SJR по году публикации; для лет после последнего опубликованного рейтинга — последний доступный (год указан).
* **Качество подбора на 10 примерах** (подразделение из заявки как ориентир): только эмбеддинги — в топ-3 у ~половины
  кандидатов, в топ-10 у 7–8 из 10; с переранжированием LLM — на 1-м месте у 8 из 10 (часть proposals прямо называет
  подразделение, поэтому эта цифра оптимистична).
* **Подбор подразделений**: 75% — семантическое сходство (локальная многоязычная модель
  `paraphrase-multilingual-MiniLM-L12-v2`, без внешних API), 25% — общие термины (TF-IDF). Подразделение из заявки
  служит проверкой: `scripts/eval_matching.py` считает, на каком месте оно оказывается (hit@k, MRR).
* Все внешние ответы кэшируются в `data/cache` (14 дней) — повторное формирование досье мгновенное.

## Персональные данные

`data/examples`, `data/dossiers`, `data/uploads`, `data/cache` содержат персональные данные кандидатов и исключены из git.

---

### English summary

Fellowship Candidate Intelligence turns a Fellowship application (CV, research proposal, Tilda form, ORCID / Scopus /
Google Scholar IDs) into a structured candidate dossier: publications of the last four years with type, journal, DOI,
author position and citations; SJR quartile with year and subject categories; H-index from Scopus, Google Scholar and
OpenAlex; consistency checks between the CV and external data; and a research profile written by Claude (optional).
Backend: Python / FastAPI. Run with the commands above.
