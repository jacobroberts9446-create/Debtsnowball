# DebtSnowball

*A Python application for automated paycheck budgeting and debt snowball planning.*

DebtSnowball builds a paycheck-by-paycheck debt payoff plan using a debt snowball strategy. It combines real calendar scheduling, savings goal tracking, debt interest calculations, and Excel reporting into a reproducible workflow driven by `config.json`.

![Python](https://img.shields.io/badge/python-3.13%20%7C%203.14-blue)
![Version](https://img.shields.io/badge/version-1.2.0--dev-yellow)
![Coverage](https://img.shields.io/badge/coverage-93%25-brightgreen)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## Features

- Paycheck-by-paycheck budgeting
- Debt snowball payoff strategy
- Automatic interest calculations
- Real calendar scheduling
- Savings goal tracking
- Excel report generation
- Dashboard worksheet
- SQLite history support
- Interactive plan setup
- Interactive savings strategy selection
- Generated plan review and save workflow
- Automated test suite
- 93% test coverage

---

## Screenshots

![Dashboard worksheet](docs/images/dashboard.png)
Dashboard worksheet placeholder.

![Pay period summary](docs/images/pay_period.png)
Pay period summary placeholder.

![Debt tracker](docs/images/debts.png)
Debt tracker placeholder.

---

## Example Workflow

```text
config.json
    |
    v
Calendar Engine
    |
    v
Scheduler
    |
    v
Budget Engine
    |
    v
Debt Engine
    |
    v
Excel Writer
    |
    v
DebtSnowball Workbook
```

The application starts with user-defined budget settings, bills, debts, and savings goals in `config.json`. It generates pay periods, assigns scheduled payments, calculates debt and savings activity, then writes the results to an Excel workbook.

---

## Installation

Clone the repository:

```bash
git clone https://github.com/jacobroberts9446-create/Debtsnoball.git
cd Debtsnoball
```

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

---

## Usage

Run the interactive application:

```bash
python run.py
```

With no arguments, DebtSnowball opens an interactive console menu. The normal
workflow is:

1. Create New Plan
2. Enter debts, bills, budget, and savings strategy
3. Review the generated forecast
4. Save the plan under a meaningful name
5. Access separate scenarios or people's plans through Saved Plans
6. View prior saved versions through History on the selected plan

The guided new-plan workflow collects plan basics, debts, recurring bills,
spending, savings, and savings strategy, then lets you review and save the
generated plan.

Existing command-line workflows remain available. Show them with:

```bash
python run.py --help
```

Run tests:

```bash
pytest
```

Run tests with coverage:

```bash
pytest --cov=app
```

Developer and automation workflows that use `config.json` remain available
through the command-line interface and source-level helpers. The interactive
menu does not present config generation as the normal user workflow.

When workbook generation is run, the workbook is written to:

```text
output/debtsnowball_plan.xlsx
```

---

## Portable Windows Build

DebtSnowball can be packaged as a portable Windows console executable. This is
not an installer; it creates a folder that can be zipped and shared.

Install build dependencies:

```bash
python -m pip install -r requirements-dev.txt
```

Build the executable on Windows:

```bash
python scripts/build_windows.py
```

The build output appears at:

```text
dist/DebtSnowball/DebtSnowball.exe
```

End users launch the portable build by opening the `dist/DebtSnowball` folder
and double-clicking `DebtSnowball.exe`. The executable opens the existing
interactive console menu and does not require Python to be installed on the
target machine.

When running from source, DebtSnowball continues to use the repository paths:

```text
config.json
output/
```

When running from the packaged executable, writable user data is stored under:

```text
%LOCALAPPDATA%/DebtSnowball
```

Packaged defaults include:

```text
%LOCALAPPDATA%/DebtSnowball/output/debtsnowball.sqlite
%LOCALAPPDATA%/DebtSnowball/output/debtsnowball_plan.xlsx
%LOCALAPPDATA%/DebtSnowball/output/preferences.json
```

The packaged app reads `%LOCALAPPDATA%/DebtSnowball/config.json` when that file
exists. Otherwise, it uses the bundled `config.json` shipped with the portable
folder. Custom icons, an installer, automatic updates, and GitHub release
packaging are planned for later milestones.

---

## Configurable Scenarios

Scenario comparisons are configured in `config.json` under the optional
`scenarios` field. `Baseline` is always generated automatically, so the list only
needs alternative scenarios.

If `scenarios` is missing, `null`, or an empty list, DebtSnowball generates a
baseline-only Scenario Comparison worksheet. It does not add default extra-payment
scenarios unless you configure them.

Supported fields:

| Field | Required | Description |
| --- | --- | --- |
| `name` | Yes | Display name for the scenario. Must be unique. |
| `extra_per_paycheck` | No | Extra money added directly to snowball funding each paycheck. Defaults to `0.00`. |
| `savings_percentage` | No | Decimal savings split override, such as `0.25` for 25%. Omit it to keep the normal savings rule. |
| `snowball_order` | No | Debt names to prioritize first. Omitted debts are appended in their original order. |

Baseline-only configuration:

```json
{
  "scenarios": []
}
```

Extra-payment scenarios:

```json
{
  "scenarios": [
    {
      "name": "Extra $75",
      "extra_per_paycheck": "75.00"
    },
    {
      "name": "Extra $150",
      "extra_per_paycheck": "150.00"
    }
  ]
}
```

Savings and payoff-order scenario:

```json
{
  "scenarios": [
    {
      "name": "Aggressive payoff",
      "extra_per_paycheck": "300.00",
      "savings_percentage": "0.00",
      "snowball_order": ["Ollo", "Citi", "Lending USA"]
    }
  ]
}
```

Common validation errors include blank or duplicate scenario names, negative or
invalid extra payments, savings percentages outside `0` to `1`, non-list
`snowball_order` values, blank or duplicate debt names, and unknown debt names.

---

## Debt-Free Target Calculator

The debt-free target calculator estimates the minimum extra payment per paycheck
needed to become debt-free by a configured target date. The extra money is
handled the same way as scenario extra payments: it goes directly to snowball
funding after normal bills, debt minimums, and savings allocation.

A target is considered successful when the projected debt-free date is on or
before the configured `target_date`. A payoff exactly on the target date counts
as success.

Configuration lives in `config.json`:

```json
{
  "debt_free_target": {
    "enabled": true,
    "target_date": "2027-12-31",
    "maximum_extra_per_paycheck": "1000.00",
    "precision": "0.01",
    "maximum_iterations": 100
  }
}
```

Supported fields:

| Field | Required | Description |
| --- | --- | --- |
| `enabled` | No | Set to `true` to run the calculator. Defaults to disabled when omitted. |
| `target_date` | Yes, when enabled | ISO date in `YYYY-MM-DD` format. |
| `maximum_extra_per_paycheck` | No | Largest extra payment to test. Defaults to `10000.00`. |
| `precision` | No | Payment increment for the search. Defaults to `0.01`. |
| `maximum_iterations` | No | Safety cap for forecast evaluations. Defaults to `100`. |

If the baseline forecast already reaches the target, the required extra payment
is `$0.00`. If there are no active debts, the calculator reports that no payoff
funding is required. If the target cannot be reached within
`maximum_extra_per_paycheck`, the result is marked unreachable and the maximum is
not reported as a required payment.

The calculator uses binary search over the configured precision instead of
testing every cent one at a time. Its forecast horizon includes the target date
and additional bounded time so a requested target is not marked unreachable just
because the default forecast window was too short.

---

## Dated Savings Plans

DebtSnowball supports optional multi-stage savings plans in `config.json`. When
`savings_plan` is omitted, the original single `savings_goal` behavior is used.
When `savings_plan` is present, dated goal stages and planned withdrawals become
the source of truth for savings targets.

Example:

```json
{
  "savings_plan": {
    "deadline_priority_enabled": false,
    "goals": [
      {
        "name": "August withdrawal",
        "target_amount": "2400.00",
        "start_date": "2026-07-17",
        "target_date": "2026-08-11",
        "funding_mode": "deadline_priority"
      }
    ],
    "withdrawals": [
      {
        "name": "August planned expense",
        "date": "2026-08-11",
        "amount": "2400.00"
      }
    ]
  }
}
```

`deadline_priority_enabled` is the explicit feature switch. When it is `false`,
configured deadline goals and withdrawals are treated as a development template:
they do not appear as active goals, do not alter the live forecast, and do not
process withdrawals.

Savings stages use the configured `target_amount` as the desired balance for
that stage. Bills and debt minimum payments are always protected before savings
priority is applied. A savings goal can only redirect discretionary snowball
cash that remains after those required payments.

Funding modes:

| Mode | Behavior |
| --- | --- |
| `percentage` | Preserves the original savings split behavior. |
| `deadline_priority` | Requires a `target_date`; recalculates the per-paycheck savings needed to reach the goal and redirects normal snowball cash to savings when needed. |
| `priority_until_funded` | Sends available post-minimum cash to savings until the target is reached, then resumes normal snowball funding. |

For deadline goals, eligible paychecks are pay dates on or after the goal
`start_date` and on or before the `target_date`. A paycheck after the deadline
does not count toward that deadline, even when a planned withdrawal is processed
on that later paycheck.

For `deadline_priority` goals, the engine first applies the normal savings
split, then redirects discretionary snowball cash. If the target still cannot be
met, it may temporarily reduce the configured personal-expense allowance by the
exact remaining amount needed across eligible paychecks. This reduction is capped
at the personal allowance, never makes personal expenses negative, and stops
after the deadline or once the goal is funded.

If the available discretionary cash is not enough to meet a deadline,
DebtSnowball does not fabricate funds or reduce required payments. It reports
the projected deadline balance, projected shortfall, whether the goal is
feasible, and the additional funding needed.

Planned withdrawals are applied at the beginning of the first paycheck period
whose processing date is on or after the withdrawal date, before that period's
new savings contribution. If a withdrawal shares a date with an outgoing goal
deadline, the outgoing goal is evaluated before the withdrawal is applied.

Withdrawal options:

| Field | Behavior |
| --- | --- |
| `drain_balance: true` | Withdraws the full available savings balance and leaves savings at `$0.00`. |
| `amount` | Withdraws up to the requested amount. If savings is lower, the actual withdrawal is capped and the shortfall is reported. |

Exactly one of `amount` or `drain_balance` must be configured. Savings never
goes negative. Withdrawals do not change paycheck income, bills, debt balances,
minimum payments, snowball order, or interest calculations.

Scenario comparisons and the debt-free target calculator reuse the same forecast
engine, so configured savings stages and planned withdrawals are honored in those
projections too. Scenario savings-percentage overrides can change contribution
rate, but they do not remove or move planned withdrawals. Scenario extra
snowball payments are treated as additional debt money after bills, minimums,
and required savings priority have already been handled.

---

## Example Output

The Excel workbook includes:

| Worksheet | Description |
| --- | --- |
| Dashboard | High-level savings, debt, payoff progress, and key metrics. |
| Pay Period Summaries | Paycheck-by-paycheck income, bills, minimums, savings, snowball payments, and remaining cash. |
| Active Debts | Debt balances over time for debts still being paid. |
| Paid-Off Debts | Debts that have reached a zero balance. |
| Savings Progress | Savings deposits, savings balance, goal, and remaining amount to goal. |
| Forecast | Future payoff, savings, interest, and balance projections. |
| Scenario Comparison | Baseline and configured scenario comparisons with deltas, payoff dates, and charts. |
| Debt-Free Target | Minimum extra payment needed to reach a configured target date. |
| Charts | Savings growth and debt reduction visualizations. |

---

## Project Structure

```text
DebtSnowball/
|
|-- app/
|   |-- bill_input.py
|   |-- budget_engine.py
|   |-- budget_setup.py
|   |-- calendar_engine.py
|   |-- cli.py
|   |-- console.py
|   |-- config.py
|   |-- database.py
|   |-- debt_engine.py
|   |-- debt_input.py
|   |-- excel_writer.py
|   |-- forecast_engine.py
|   |-- history/
|   |-- menu.py
|   |-- models.py
|   |-- plan_generation.py
|   |-- plan_save.py
|   |-- plan_setup.py
|   |-- results_viewer.py
|   |-- savings_setup.py
|   |-- scenario_engine.py
|   |-- serialization.py
|   |-- target_calculator.py
|   `-- scheduler.py
|-- tests/
|-- output/
|-- docs/
|-- config.json
|-- run.py
`-- README.md
```

- `app/`: Application engines, models, configuration loading, database support, and Excel generation.
- `tests/`: Automated pytest suite.
- `output/`: Generated workbook output.
- `docs/`: Documentation assets such as screenshots.
- `config.json`: Budget, bill, debt, and savings inputs.
- `run.py`: Application entry point.

---

## Architecture

| Component | Responsibility |
| --- | --- |
| CalendarEngine | Generates biweekly pay periods from the configured first paycheck date. |
| Scheduler | Assigns bills and debt minimums to the last paycheck before their due date. |
| BudgetEngine | Orchestrates each pay period and produces structured `PayPeriodSummary` objects. |
| DebtEngine | Accrues interest, pays minimums, applies snowball payments, and tracks paid-off debts. |
| ForecastEngine | Simulates future pay periods without mutating the live configuration. |
| ScenarioEngine | Compares baseline forecasts against configured alternative scenarios. |
| DebtFreeTargetCalculator | Finds the minimum extra snowball payment needed for a target debt-free date. |
| ExcelWriter | Creates the Excel workbook, dashboard worksheet, data worksheets, and charts. |
| Database | Provides SQLite persistence support for generated budget history. |
| PlanHistoryService | Saves local plan versions, forecast snapshots, actual entries, comparisons, and exports. |
| Interactive setup modules | Collect plan basics, debts, bills, spending, savings, and strategy through the console. |
| ResultsViewer | Displays generated in-memory forecasts without rerunning the engine. |
| PlanSave | Saves the reviewed generated forecast through the history service. |

---

## Money Handling

DebtSnowball uses Python `Decimal` values for financial calculations. Money is
rounded to cents with `ROUND_HALF_UP` through the centralized helpers in
`app/money.py`.

Preferred configuration format for money is a quoted string:

```json
{
  "amount": "215.00"
}
```

JSON numbers remain supported for backward compatibility, but new monetary
fields should use strings to preserve exact decimal intent.

Project money rules:

- Financial calculations must remain `Decimal`.
- New money fields should be normalized with `money()`.
- User-facing currency should use `format_currency()`.
- JSON output serializes `Decimal` money as fixed two-decimal strings, such as `"215.00"`.
- `Decimal("-0.00")` is normalized to `Decimal("0.00")`.
- A remaining debt balance of exactly `$0.01` is treated as paid; `$0.02` is not forgiven.
- Excel receives numeric values only at the writer boundary through `excel_number()`.
- SQLite stores monetary values as integer cents through `to_cents()` and `from_cents()`.

SQLite schema versioning uses `PRAGMA user_version`.

| Version | Meaning |
| --- | --- |
| `0` or `1` | Legacy schema with money stored as SQLite `REAL`. |
| `2` | Current schema with money stored as SQLite `INTEGER` cents. |

When a legacy database is opened, DebtSnowball creates a timestamped backup next
to the original database before running the schema migration. Backups use a name
like `debtsnowball.sqlite.v1-real-money.20260722153000.bak`. In-memory databases
are not backed up.

Legacy money is converted with the same application policy used everywhere else:
values are parsed through Decimal, rounded to cents with `ROUND_HALF_UP`, then
stored as integer cents. For example:

| Money | SQLite cents |
| --- | ---: |
| `$0.01` | `1` |
| `$215.00` | `21500` |
| `$568.50` | `56850` |
| `$2,400.00` | `240000` |

The maximum supported SQLite money value is bounded by SQLite's signed 64-bit
integer range for cents. Values outside that range raise an error instead of
being stored.

Developer rules for future database work:

- Never write `Decimal` money to SQLite as `REAL`.
- Always use `to_cents()` when writing monetary values.
- Always use `from_cents()` when reading monetary values.
- Keep percentages and rates, such as APR, separate from money storage.
- Future schema changes should add a new `PRAGMA user_version` migration.
- If a migration fails, keep the `.bak` file, fix the source data or schema, and rerun the application.

---

## Local Plan History

DebtSnowball can keep durable local plan history in SQLite. In the interactive
app, Saved Plans represent separate scenarios or separate people, such as
`My Aggressive Plan`, `My Conservative Plan`, or `Mom's Debt Plan`.

History belongs to one selected saved plan and represents that plan's prior
saved versions. Open `Saved Plans`, choose a plan by name, then choose
`View History`.

Optional history features include:

- Named plans with immutable versions.
- Decimal-safe configuration snapshots.
- SHA-256 configuration and forecast fingerprints.
- Forecast snapshots with period, debt, and savings detail.
- Actual activity entries kept separate from forecast assumptions.
- Reversal entries for corrections, preserving an audit trail.
- Neutral plan comparison and forecast-versus-actual status labels.
- Portable JSON export and CSV forecast-period export.

Useful commands:

```bash
python run.py plan save --name "Current Plan"
python run.py plan list
python run.py plan history --plan-id 1
python run.py plan compare --from-version 1 --to-version 2
python run.py plan restore --version 1
python run.py actual add --plan-id 1 --date 2026-07-17 --type debt_payment --amount 215.00
python run.py actual summary --plan-id 1
python run.py export --plan-id 1 --path output/current-plan.json
python run.py import --path output/current-plan.json --name "Imported Plan"
```

SQLite schema version `3` adds:

- `plans`
- `plan_versions`
- `forecast_snapshots`
- `forecast_periods`
- `debt_snapshots`
- `savings_snapshots`
- `actual_transactions`

Version `2` databases migrate to version `3` without deleting existing paycheck
or debt rows. File-backed databases receive a timestamped backup before the
schema migration.

Privacy notes:

- DebtSnowball keeps plan and actual-history data local by default.
- It does not connect to bank accounts.
- It does not upload financial data.
- It does not sell or share user information.
- Database backups and exported files may contain sensitive financial data.
- Users should protect SQLite, backup, JSON, CSV, and workbook files.

---

## Testing

DebtSnowball uses `pytest` for automated testing.

```bash
pytest
```

Coverage is measured with `pytest-cov`:

```bash
pytest --cov=app
```

Current coverage: **93%**

---

## Roadmap

| Version | Status | Focus |
| --- | --- | --- |
| Version 1.0 | Complete | Core scheduling, debt snowball calculations, savings tracking, Excel workbook generation, dashboard worksheet, and automated tests. |
| Version 1.1 | Release candidate | Interactive plan setup, debt and bill entry, savings strategy selection, results review, and generated-plan saving. |
| Version 2 | In development | Forecast Engine, Scenario Comparison, configurable scenarios, Debt-Free Target Calculator, dated savings goals, and deadline-aware savings priority. |
| Version 3 | Future | Deeper analytics, richer charts, and additional user-interface polish. |

---

## Contributing

Contributions are welcome.

1. Fork the repository.
2. Create a feature branch.
3. Keep changes focused and covered by tests.
4. Run `pytest` and `pytest --cov=app`.
5. Open a pull request with a clear description of the change.

Please avoid combining unrelated refactors, new features, and bug fixes in the same pull request.

---

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

---

## Author

Jacob Roberts

