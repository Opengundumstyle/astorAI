import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LandedCostWaterfall } from "../LandedCostWaterfall";
import type { LandedCost } from "@/lib/types";

const OPS: LandedCost = {
  currency: "USD", qty: 2, ex_works: 16.8, tariff: 4.2, duty_rate: 0.25,
  freight: 1.5, margin: 4.5, unit_price: 27.0, line_total: 54.0,
};

const BUYER: LandedCost = { currency: "USD", qty: 2, unit_price: 27.0, line_total: 54.0 };

describe("LandedCostWaterfall", () => {
  it("renders the full ops breakdown including duty rate", () => {
    render(<LandedCostWaterfall data={OPS} />);
    expect(screen.getByText(/ex-works/i)).toBeInTheDocument();
    expect(screen.getByText(/tariff/i)).toBeInTheDocument();
    expect(screen.getByText(/25%/)).toBeInTheDocument(); // duty_rate formatted
    expect(screen.getByText("$27.00")).toBeInTheDocument(); // unit price
    expect(screen.getByText("$54.00")).toBeInTheDocument(); // line total
  });

  it("hides internal rows in buyer mode (price only)", () => {
    render(<LandedCostWaterfall data={BUYER} />);
    expect(screen.queryByText(/ex-works/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/tariff/i)).not.toBeInTheDocument();
    expect(screen.getByText("$27.00")).toBeInTheDocument();
  });
});
