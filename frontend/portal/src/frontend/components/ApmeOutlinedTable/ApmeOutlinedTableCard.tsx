/*
 * Copyright Red Hat
 *
 * Shared outlined table chrome used by Quality activity and Dependencies.
 */

import type { ReactNode } from "react";
import { Box, Card, makeStyles } from "@material-ui/core";

const useStyles = makeStyles((theme) => ({
  table: {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: 13,
    "& thead": {
      backgroundColor:
        theme.palette.type === "dark" ? "rgba(255,255,255,0.04)" : "#f5f5f5",
      borderBottom: `1px solid ${theme.palette.divider}`,
    },
    "& th": {
      textAlign: "left",
      padding: "10px 12px",
      fontWeight: 600,
      fontSize: 12,
      color: theme.palette.text.secondary,
      textTransform: "uppercase",
      letterSpacing: 0.3,
      whiteSpace: "nowrap",
    },
    "& td": {
      padding: "10px 12px",
      borderBottom: `1px solid ${theme.palette.divider}`,
      verticalAlign: "middle",
    },
    "& tbody tr:last-child td": {
      borderBottom: "none",
    },
    "& tbody tr:hover": {
      backgroundColor: theme.palette.action.hover,
    },
  },
  sortableHeader: {
    cursor: "pointer",
    userSelect: "none",
    "&:hover": { color: theme.palette.text.primary },
  },
}));

export function useApmeOutlinedTableStyles() {
  return useStyles();
}

export function ApmeOutlinedTableCard({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <Card variant="outlined" style={{ borderRadius: 8, overflow: "hidden" }}>
      <Box style={{ overflow: "auto" }}>{children}</Box>
    </Card>
  );
}
