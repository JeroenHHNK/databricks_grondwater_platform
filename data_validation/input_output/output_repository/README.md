# input_output/output_repository/: the validation store (layer 3)

The human review decisions: one store per series, `<filename>.csv`, with the same stem as
the source file in `../output_databricks/time_series_data/`. This is the asset. It is
git-tracked, **merged into and never replaced**, and read back into
`waterkeringbeheer_prd.jvanruitenbeek.validated_meting` by
`databricks_code/notebooks/03_validated_to_delta.py`.

```
input_output/output_databricks/   layer 1, source copy: read-only, overwritten by each export
      |  local_validation/validate.ipynb  (automated flags -> output_data/, layer 2)
      |  streamlit_app.py                 (manual review)
      v
input_output/output_repository/   layer 3 --push--> Databricks --03--> validated_meting
```

Frozen schema (plus `correctie`), UTF-8 with BOM:

```csv
timestamp,raw_data,gecontroleerd,afgekeurd,note,correctie
2024-01-01 00:00:00,-1.2345,1,0,,0.0
2024-01-01 01:00:00,-0.4000,1,1,sensor spike,0.0
2024-01-01 02:00:00,-4.0200,0,1,auto: rule B,0.0
2024-03-01 00:00:00,-1.3345,1,0,sensor verhangen,0.1
```

- `correctie` (m) is a manual offset, set per period in the review app (*Correction*). The
  validated head is `raw_data + correctie`; `raw_data` itself is never changed. Stores saved
  before the column existed load with `correctie = 0`.
- `gecontroleerd = 1`: a human reviewed the row. `afgekeurd = 1`: the row is rejected.
- `afgekeurd = 1` with `gecontroleerd = 0` is an autoflag rejection that no human has
  confirmed yet (note `auto: ...`).
- A new export never changes a reviewed row; unreviewed rows take the new value, and new
  hours are appended as unreviewed (`local_validation/stores.py`).
- Stores are created when a series is first saved in the review app, or for all series with
  `stores.sync_stores(create_missing=True)`.
- `03` also still accepts the on-prem nested `<vendor>/<origin>/<series>.csv` layout.

Commit only this folder after a review session.
