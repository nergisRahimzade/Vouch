# Held-out set

This folder holds the held-out planted mistakes and their answer key. They are written in a separate session from plain descriptions, are never read by Vouch's agent code or prompts, and are only scored, never tuned on. Vouch's builders do not create or edit anything here except this README.

## Apply them

```bash
python -m vouch.world build --heldout            # uses ./heldout
python -m vouch.world build --heldout path/to/dir
```

This builds the same company with the held-out plants applied to the open month, instead of the main set. The noise stays. Any `answer_key.json` here is copied to `sealed/heldout_answer_key.json` for the evaluator.

## Interface

Each case lives in a Python module in this folder (any name not starting with `_`):

```python
from vouch.world.plants import Plant, OPEN          # plus any helpers from vouch.world.plants
from vouch.world.simulate import Casting            # optional

def my_case(plant_id, planter):
    plant = Plant(plant_id, "kind_of_mistake", OPEN)

    @plant.on(OPEN, "payments")                      # stage: hr | register | payments | equity | bank | ledger
    def _(ctx):                                      # ctx: month, world, bundle, rows(name), master, state
        ...                                          # change the source rows for this one mistake
        plant.key.update(...)                        # optional: what the plant recorded

    return plant

PLANTS = [("HELD-01", my_case)]                       # ids must not clash with the main set
CASTINGS = []                                         # optional: Casting(plant_id, role, hire_month, ...)
```

Rules the build enforces:

- A held-out plant may act on the open month only. A hook on any other month is refused.
- Plant ids must be unique and must not reuse a main-set id.
- Use `planter.free(employee_id, plant_id)` and `planter.claim(employee_id)` when choosing people, so noise doesn't land on the same person.
