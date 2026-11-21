---
name: statistics-in-python
description: Run an analysis in run_python when the statistics tool cannot express it (custom recoding, logistic or multiple regression with interactions, effect sizes, bootstraps, sample-size calculations), and check its numbers.
category: research
version: "1.0"
type: recipe
triggers: "logistic regression, multiple regression, bootstrap, sample size calculation, power analysis, custom analysis"
---

# Statistics in Python

Start with the `statistics` tool: it reads the table, resolves columns by name or
number and reports tests with their assumptions. Move to `run_python` when the
analysis needs something it does not offer, and keep the tool's `overview` as the
map of columns.

## Get the data into the script

- CSV or TSV: `csv.DictReader(open("attachments/data.csv", encoding="utf-8-sig"))`.
- Excel, ODS or a Word table: `statistics(path, "overview")` from `dikdik_tools`
  shows the columns, or `read_document(path)` returns the table text. If pandas is
  installed, `pandas.read_excel` is simplest; check with `import importlib.util;
  print(importlib.util.find_spec("pandas"))` before relying on it.
- Recode explicitly (for example Likert labels to 1–5) and print the counts of
  each code, so a typo in a label cannot silently become missing data.

## Without third-party packages

The standard library covers a lot: `statistics.mean`, `stdev`, `median`,
`correlation`, `linear_regression`, `NormalDist().cdf` and `.inv_cdf` for z-tests
and confidence intervals, and `random` for bootstraps and permutation tests:

```python
import random, statistics as st
a, b = [...], [...]
observed = st.mean(a) - st.mean(b)
pooled, count = a + b, 0
for _ in range(10000):
    random.shuffle(pooled)
    if abs(st.mean(pooled[:len(a)]) - st.mean(pooled[len(a):])) >= abs(observed):
        count += 1
print("difference", round(observed, 3), "permutation p", round((count + 1) / 10001, 4))
```

## With scipy or statsmodels (when installed)

- `scipy.stats`: `ttest_ind(..., equal_var=False)`, `mannwhitneyu`, `chi2_contingency`,
  `fisher_exact`, `pearsonr`, `spearmanr`, `shapiro`, `kruskal`.
- `statsmodels.formula.api`: `logit("outcome ~ age + C(sex) + score", data).fit()`
  for odds ratios (`numpy.exp(model.params)` and `conf_int()`), `ols(...)` with
  interactions (`a * b`).
- Installing a package is a separate command (`pip install ...`) that the user
  approves; say why it is needed.

## Sample size

For a proportion: `n = z² · p(1 − p) / e²` with `z = NormalDist().inv_cdf(1 − α/2)`;
apply the finite population correction `n / (1 + (n − 1) / N)` when the population
N is known. Print each input with the result.

## Check before reporting

- Print n for every analysis and compare it with the data's row count and missing values.
- Re-derive one key number a second way (a mean by hand, a crosstab total).
- Report the test, statistic, degrees of freedom, p-value, effect size and CI,
  copied from the printed output, never retyped from memory.
