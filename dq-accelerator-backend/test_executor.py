import pandas as pd
import dqa.checks
from dqa.rules.executor import RuleExecutor
from dqa.models import Rule

rule = Rule(
    id="test_rule",
    name="Test Rule",
    dimension="completeness",
    check="not_null",
    severity="high",
    column="my_col"
)

df = pd.DataFrame({"my_col": ["a", None, "b", "", "c"]})
df["__row__"] = df.index + 1

executor = RuleExecutor([rule], {"profile": None, "run_id": "test"})
executor.process_chunk(df)
results, violations, valid_examples = executor.finalise()

print("Violations:")
for v in violations["test_rule"]:
    print(v)

print("\nValid:")
for v in valid_examples["test_rule"]:
    print(v)
