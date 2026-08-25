import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  Badge,
  Button,
  Card,
  CardContent,
  Skeleton,
  Spinner,
  Table,
  TableBody,
  TableCell,
  TableRow,
} from "@kortex/design-system";

describe("@kortex/design-system consumption", () => {
  it("renders foundation components imported from the workspace package", () => {
    render(
      <Card>
        <CardContent>
          <Button>Save</Button>
          <Badge variant="secondary">Draft</Badge>
        </CardContent>
      </Card>,
    );

    expect(screen.getByRole("button", { name: "Save" })).toBeInTheDocument();
    expect(screen.getByText("Draft")).toBeInTheDocument();
  });

  it("also renders M4-completion components (Table, Skeleton, Spinner) via the same root entry", () => {
    // Regression guard for the two-level `export *` bug fixed in index.ts —
    // covers components added after that fix, not just the original set.
    render(
      <>
        <Table>
          <TableBody>
            <TableRow>
              <TableCell>Row value</TableCell>
            </TableRow>
          </TableBody>
        </Table>
        <Skeleton data-testid="skeleton" />
        <Spinner />
      </>,
    );

    expect(screen.getByText("Row value")).toBeInTheDocument();
    expect(screen.getByTestId("skeleton")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });
});
