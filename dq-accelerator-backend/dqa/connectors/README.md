# External systems

One folder per system the accelerator can pull data from. Everything
specific to a system — how it authenticates, how it lists what can be
assessed, how it downloads records, and its API endpoints — lives in that
folder and nowhere else.

```
connectors/
  base.py          the contract every system implements
  __init__.py      looks a system up by the `source.type` a request names
  salesforce/      Salesforce (API contract revision 5)
```

## What a system provides

A system's job ends when an object's records are on disk as a CSV. From
there the object is an ordinary run: it is profiled, paused for CDE
confirmation, evaluated and scored by exactly the pipeline an uploaded file
goes through. That is what keeps scores comparable across uploads and every
system, and it is why nothing under `connectors/` knows about dimensions or
rules.

Each system folder contains:

| Module | Responsibility |
| --- | --- |
| `source.py` | `plan(source, objects, assessment_id)` — validate a selection and return an `AssessmentPlan` (`base.py`) |
| `routes.py` | The system's own endpoints, such as connecting and listing objects |
| anything else | The system's internals, named for what they do |

`AssessmentPlan.close(reason)` must release every credential the system
holds. It is called once no further access is needed: after the last object
is downloaded, or when the assessment is deleted.

## Adding a system

1. Create `connectors/<system>/` with `source.py` and `routes.py`.
2. Register it in `connectors/__init__.py`: add it to `_systems()` and its
   router to `routers()`.
3. Add its settings to `dqa/config.py` under *External systems*, prefixed
   with the system's name.
4. Add its tests as `tests/test_<system>.py`, against a fake of the system
   rather than a live one — see `tests/fake_salesforce.py`.

Nothing outside `connectors/` should need to change.

## Credentials

Credentials are held in memory only, never written to disk or logs, and
released by `close()`. A system that cannot meet that is not ready to add.
