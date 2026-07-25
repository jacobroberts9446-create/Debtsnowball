# Changelog

All notable changes to this project will be documented in this file.

## [1.2.0-dev] - Unreleased

### Added
- Portable Windows executable launcher build
- Packaged-app path handling for bundled resources and writable user data
- PyInstaller one-folder build configuration
- Interactive activity recording for income, bills, debt payments, savings,
  personal spending, and adjustments

## [1.1.0] - Unreleased

### Added
- Interactive plan setup workflow
- Interactive debt and recurring bill entry
- Interactive savings strategy selection
- In-memory generated results viewer
- Save Generated Plan workflow backed by plan history

### Fixed
- Saved generated plans now preserve the effective savings allocation used by the reviewed forecast
- Generated-plan saves now persist plan/version data and forecast snapshots atomically
- Interactive save workflow now catches only expected validation and persistence errors
- Version metadata and coverage documentation aligned for v1.1.0

## [1.0.0] - Unreleased

### Added
- Exact Decimal-based financial calculations
- Integer-cent SQLite money storage
- Debt snowball forecasting
- Savings and deadline-goal support
- Excel workbook generation
- Immutable plan history
- Forecast snapshots
- Actual transaction tracking
- Plan comparison
- JSON import/export
- Database migration support

### Fixed
- Safe staged schema migrations
- Strict import validation
- Stable duplicate-import detection
- Forecast reconciliation
- Debt snapshot cent-level consistency
- Plan-version immutability

### Security
- Import tampering detection
- Transaction-safe import rollback
- Database integrity validation
