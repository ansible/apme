/*
 * Copyright Red Hat
 *
 * Empty state when git repositories exist but none have been scanned.
 */

import { Box, Typography, makeStyles } from "@material-ui/core";

const useStyles = makeStyles((theme) => ({
  root: {
    textAlign: "center",
    padding: theme.spacing(6, 3),
  },
  title: {
    fontWeight: 600,
    marginBottom: theme.spacing(1),
  },
  description: {
    fontSize: 14,
    color: theme.palette.text.secondary,
    maxWidth: 480,
    marginLeft: "auto",
    marginRight: "auto",
  },
}));

export const FleetQualityNoScansEmptyState = () => {
  const classes = useStyles();

  return (
    <Box className={classes.root}>
      <Typography variant="h6" className={classes.title}>
        No scans yet
      </Typography>
      <Typography className={classes.description}>
        Start a scan to check your Git repositories for quality issues.
      </Typography>
    </Box>
  );
};
