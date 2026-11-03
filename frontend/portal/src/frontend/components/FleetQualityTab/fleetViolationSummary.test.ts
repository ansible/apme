import { formatFleetViolationSummary } from "./fleetViolationSummary";

describe("formatFleetViolationSummary", () => {
  it("formats unfiltered fleet totals", () => {
    expect(
      formatFleetViolationSummary({
        hasFilter: false,
        violationTotal: 10,
        filteredRuleCount: 2,
        allRulesCount: 5,
        reposWithIssues: 3,
      }),
    ).toBe("violations · 5 rules · 3 repositories");
  });

  it("formats filtered totals with singular rule label", () => {
    expect(
      formatFleetViolationSummary({
        hasFilter: true,
        violationTotal: 10,
        filteredRuleCount: 1,
        allRulesCount: 5,
        reposWithIssues: 3,
      }),
    ).toBe("of 10 violations · 1 rule · 3 repositories");
  });

  it("formats filtered totals with plural rule label", () => {
    expect(
      formatFleetViolationSummary({
        hasFilter: true,
        violationTotal: 10,
        filteredRuleCount: 2,
        allRulesCount: 5,
        reposWithIssues: 3,
      }),
    ).toBe("of 10 violations · 2 rules · 3 repositories");
  });
});
