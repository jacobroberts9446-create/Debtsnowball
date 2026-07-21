# 💰 DebtSnowball

*A Python application for automated paycheck budgeting and debt snowball planning.*

DebtSnowball builds a paycheck-by-paycheck debt payoff plan using a debt snowball strategy. It combines real calendar scheduling, savings goal tracking, debt interest calculations, and Excel reporting into a reproducible workflow driven by `config.json`.

![Python](https://img.shields.io/badge/python-3.14-blue)
![Version](https://img.shields.io/badge/version-1.0.0-brightgreen)
![Coverage](https://img.shields.io/badge/coverage-97%25-brightgreen)
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
- Automated test suite
- 97% test coverage

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
    ↓
Calendar Engine
    ↓
Scheduler
    ↓
Budget Engine
    ↓
Debt Engine
    ↓
Excel Writer
    ↓
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

Run the application:

```bash
python run.py
```

Run tests:

```bash
pytest
```

Run tests with coverage:

```bash
pytest --cov=app
```

The generated workbook is written to:

```text
output/debtsnowball_plan.xlsx
```

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
| Charts | Savings growth and debt reduction visualizations. |

---

## Project Structure

```text
DebtSnowball/
│
├── app/
│   ├── budget_engine.py
│   ├── calendar_engine.py
│   ├── config.py
│   ├── database.py
│   ├── debt_engine.py
│   ├── excel_writer.py
│   ├── models.py
│   └── scheduler.py
├── tests/
├── output/
├── docs/
├── config.json
├── run.py
└── README.md
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
| ExcelWriter | Creates the Excel workbook, dashboard worksheet, data worksheets, and charts. |
| Database | Provides SQLite persistence support for generated budget history. |

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

Current coverage: **97%**

---

## Roadmap

| Version | Status | Focus |
| --- | --- | --- |
| Version 1.0 ✅ | Complete | Core scheduling, debt snowball calculations, savings tracking, Excel workbook generation, dashboard worksheet, and automated tests. |
| Version 2 🚧 | In development | Forecast Engine, forecast workbook reporting, Scenario Comparison Engine complete, and Scenario Comparison Reporting in development. |
| Version 3 🔮 | Future | User interface, deeper analytics, richer charts, saved history workflows, and interactive planning tools. |

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

MIT License placeholder. A full `LICENSE` file has not been added yet.

---

## Author

Jacob Roberts
