"""
target_calculator.py

Finds the minimum extra snowball payment needed to become debt-free by a
requested target date.
"""

from copy import deepcopy
from datetime import date
from decimal import Decimal, ROUND_CEILING

from app.forecast_engine import ForecastEngine
from app.money import money, to_decimal
from app.models import (
    DebtFreeTargetIteration,
    DebtFreeTargetRequest,
    DebtFreeTargetResult,
    DebtFreeTargetStatus,
    ForecastSummary,
)


class DebtFreeTargetCalculator:
    """Calculate minimum extra snowball funding for a debt-free target date."""

    def __init__(self, config: object, request: DebtFreeTargetRequest) -> None:
        self.config = config
        self.request = request
        self.iterations: list[DebtFreeTargetIteration] = []

    def calculate(self) -> DebtFreeTargetResult:
        """Return the minimum extra per paycheck needed to meet the target date."""
        self._validate_request()
        target_date = self.request.target_date
        max_extra = self._money(self.request.maximum_extra_per_paycheck)
        precision = self._precision(self.request.precision)

        baseline = self._evaluate(Decimal("0.00"))
        if self._has_no_active_debt():
            return self._result(
                target_date=target_date,
                required_extra=Decimal("0.00"),
                forecast=baseline,
                baseline=baseline,
                lower_bound=Decimal("0.00"),
                upper_bound=Decimal("0.00"),
                status=DebtFreeTargetStatus.NO_DEBT,
                message="No payoff funding is required because there are no active debts.",
            )

        if self._meets_target(baseline):
            return self._result(
                target_date=target_date,
                required_extra=Decimal("0.00"),
                forecast=baseline,
                baseline=baseline,
                lower_bound=Decimal("0.00"),
                upper_bound=Decimal("0.00"),
                status=DebtFreeTargetStatus.ALREADY_ON_TRACK,
                message="Baseline forecast is already debt-free on or before the target date.",
            )

        maximum = self._evaluate(max_extra)
        if not self._meets_target(maximum):
            return self._result(
                target_date=target_date,
                required_extra=None,
                forecast=maximum,
                baseline=baseline,
                lower_bound=Decimal("0.00"),
                upper_bound=max_extra,
                status=DebtFreeTargetStatus.UNREACHABLE,
                message=(
                    "Target cannot be reached within the configured maximum extra "
                    "payment."
                ),
            )

        max_units = self._units(max_extra, precision)
        lower_units = 0
        upper_units = max_units
        best_units = max_units
        best_forecast = maximum

        while lower_units <= upper_units:
            self._guard_iterations()
            midpoint = (lower_units + upper_units) // 2
            candidate = self._amount_from_units(midpoint, precision)
            forecast = self._evaluate(candidate)

            if self._meets_target(forecast):
                best_units = midpoint
                best_forecast = forecast
                upper_units = midpoint - 1
            else:
                lower_units = midpoint + 1

        required_extra = self._amount_from_units(best_units, precision)
        lower_test = max(required_extra - precision, Decimal("0.00"))
        if lower_test != required_extra:
            lower_forecast = self._evaluate(lower_test)
            if self._meets_target(lower_forecast):
                raise RuntimeError("target search did not find the minimum payment.")

        return self._result(
            target_date=target_date,
            required_extra=required_extra,
            forecast=best_forecast,
            baseline=baseline,
            lower_bound=lower_test,
            upper_bound=required_extra,
            status=DebtFreeTargetStatus.TARGET_MET,
            message="Target can be reached at the returned extra payment.",
        )

    def _evaluate(self, extra_per_paycheck: Decimal) -> ForecastSummary:
        forecast = ForecastEngine(
            deepcopy(self.config),
            max_years=self._calculation_horizon_years(),
            extra_snowball_per_paycheck=self._money(extra_per_paycheck),
        ).forecast()
        self.iterations.append(
            DebtFreeTargetIteration(
                extra_per_paycheck=self._money(extra_per_paycheck),
                projected_debt_free_date=forecast.debt_free_date,
                target_met=self._meets_target(forecast),
            )
        )
        return forecast

    def _result(
        self,
        target_date: date,
        required_extra: Decimal | None,
        forecast: ForecastSummary,
        baseline: ForecastSummary,
        lower_bound: Decimal,
        upper_bound: Decimal,
        status: DebtFreeTargetStatus,
        message: str,
    ) -> DebtFreeTargetResult:
        return DebtFreeTargetResult(
            target_date=target_date,
            required_extra_per_paycheck=required_extra,
            projected_debt_free_date=forecast.debt_free_date,
            target_met=status != DebtFreeTargetStatus.UNREACHABLE,
            total_interest=forecast.total_interest_paid,
            total_snowball_paid=forecast.total_snowball_payments,
            ending_debt=forecast.remaining_debt,
            iterations_used=len(self.iterations),
            lower_bound_tested=self._money(lower_bound),
            upper_bound_tested=self._money(upper_bound),
            maximum_extra_tested=self._money(self.request.maximum_extra_per_paycheck),
            precision=self._precision(self.request.precision),
            calculation_status=status,
            message=message,
            baseline_debt_free_date=baseline.debt_free_date,
            baseline_total_interest=baseline.total_interest_paid,
            baseline_total_snowball_paid=baseline.total_snowball_payments,
            baseline_ending_debt=baseline.remaining_debt,
            iterations=list(self.iterations),
        )

    def _validate_request(self) -> None:
        if self.request.target_date is None:
            raise ValueError("target_date is required.")
        if not isinstance(self.request.target_date, date):
            raise ValueError("target_date must be a date.")
        if self.request.target_date < self.config.settings.first_paycheck:
            raise ValueError("target_date cannot be before the forecast start date.")

        maximum = self._money(self.request.maximum_extra_per_paycheck)
        if maximum < Decimal("0.00"):
            raise ValueError("maximum_extra_per_paycheck cannot be negative.")
        self._precision(self.request.precision)

        if self.request.maximum_iterations <= 0:
            raise ValueError("maximum_iterations must be positive.")

    def _guard_iterations(self) -> None:
        if len(self.iterations) >= self.request.maximum_iterations:
            raise RuntimeError("maximum target calculation iterations exceeded.")

    def _has_no_active_debt(self) -> bool:
        return all(debt.balance <= 0 for debt in self.config.debts)

    def _meets_target(self, forecast: ForecastSummary) -> bool:
        return (
            forecast.debt_free_date is not None
            and forecast.debt_free_date <= self.request.target_date
        )

    def _calculation_horizon_years(self) -> int:
        start = self.config.settings.first_paycheck
        target = self.request.target_date
        days = max((target - start).days, 0)
        target_years = int(
            (Decimal(days) / Decimal("365")).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        return max(30, target_years + 5)

    def _units(self, amount: Decimal, precision: Decimal) -> int:
        return int((amount / precision).to_integral_value(rounding=ROUND_CEILING))

    def _amount_from_units(self, units: int, precision: Decimal) -> Decimal:
        return self._money(Decimal(units) * precision)

    def _precision(self, value) -> Decimal:
        precision = self._decimal(value, "precision")
        if precision <= Decimal("0.00"):
            raise ValueError("precision must be greater than zero.")
        return precision

    def _money(self, value) -> Decimal:
        return money(value)

    def _decimal(self, value, label: str) -> Decimal:
        try:
            decimal_value = to_decimal(value)
        except ValueError as exc:
            if "finite" in str(exc):
                raise ValueError(f"{label} must be finite.") from exc
            raise ValueError(f"{label} must be a valid decimal value.") from exc

        return decimal_value
