# Test applications (fictional candidates)

All people here are **invented** (names, e-mails at example.org, fake phones), so these files can be shared and
committed freely. Regenerate them with `uv run python scripts/make_test_files.py`.

Upload them at **Новая заявка**: put `CV.*` into **CV**, `Proposal.*` into **Research proposal** and
`Заявка.docx` into **Анкета заявки**, then press **Сформировать досье** (about 30–60 seconds).

| Folder | What it tests | What you should see |
|---|---|---|
| `01_EN_photonics_postdoc` | English Tilda form, PDF CV and proposal, a **broken Scopus link**, h-index claimed in the CV | Name, track (Postdoc), age, host unit filled from the form; warning that the Scopus link has no Author ID; the CV's h-index listed as "cannot be verified"; AI profile about metasurfaces; ITMO units around **nanophotonics / physics of nanostructures** |
| `02_RU_ai_medicine_fellowship` | Short **Russian** form, Russian CV and proposal (DOCX), Russian h-index phrase ("Индекс Хирша по Scopus — 9") | Everything read from Russian text; AI profile in Russian; ITMO units around **artificial intelligence**; the unit named in the form ("Институт искусственного интеллекта") found in the catalog |
| `03_DOC_chemistry_micro_fellowship` | **Legacy .doc** CV (Word 97), abbreviation in the form ("SCAMT") | The .doc is read; ITMO units around **chemistry / SCAMT / ChemBio**; "SCAMT" resolved to the SCAMT laboratory |
| `04_TOO_BIG_upload_limit` | A 12 MB file (created by the script, not stored in git) | Upload refused: "Файл больше 10 МБ" |

## Why these dossiers have no publications

The candidates do not exist, so ORCID, Google Scholar and OpenAlex have nothing about them: the publication list,
quartiles and h-index stay empty, and the tool warns that no ORCID / Scholar profile was given. That is the
expected, honest behaviour.

To test the **publication and metrics** part, you need no files at all: open **Новая заявка**, leave the
files empty and paste only the **Google Scholar link** or **ORCID** of any real researcher (for example your
professor or an ITMO scientist). Compare the dossier with their public profile.
