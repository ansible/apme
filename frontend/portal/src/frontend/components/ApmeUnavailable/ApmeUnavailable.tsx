/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { Typography, makeStyles } from "@material-ui/core";
import ErrorOutlineIcon from "@material-ui/icons/ErrorOutline";

const useStyles = makeStyles((theme) => ({
  root: {
    padding: theme.spacing(4),
    textAlign: "center",
    color: theme.palette.text.secondary,
  },
  icon: {
    fontSize: 48,
    marginBottom: theme.spacing(2),
    color: theme.palette.warning.main,
  },
}));

export interface ApmeUnavailableProps {
  message?: string;
}

/** Friendly empty state when the APME service is unreachable. */
export const ApmeUnavailable = ({
  message = "Ansible content modernization is temporarily unavailable.",
}: ApmeUnavailableProps) => {
  const classes = useStyles();

  return (
    <div className={classes.root}>
      <ErrorOutlineIcon className={classes.icon} />
      <Typography variant="h6" gutterBottom>
        APME unavailable
      </Typography>
      <Typography variant="body2">{message}</Typography>
    </div>
  );
};
