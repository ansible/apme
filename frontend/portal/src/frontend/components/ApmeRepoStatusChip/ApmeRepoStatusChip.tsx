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

import { useEffect, useState } from "react";
import { useApi } from "@backstage/core-plugin-api";
import { Chip, CircularProgress, makeStyles } from "@material-ui/core";
import { useNavigate } from "react-router-dom";
import { Project } from "../../../common/types";
import {
  projectHasActiveOperation,
  shouldResumeScanUi,
} from "../../../common/operationStatus";
import {
  chipStyleForSeverity,
  projectWorstSeverity,
} from "../../../common/severity";
import { apmeApiRef } from "../../api";
import { useApmeEnabled } from "../../hooks/useApmeEnabled";

/** While a scan is in flight. */
const ACTIVE_POLL_MS = 3000;
/** Idle catalog refresh — avoid hammering operation/state (404 when no op). */
const IDLE_POLL_MS = 30000;

const useStyles = makeStyles((theme) => ({
  clean: {
    backgroundColor: theme.palette.success.main,
    color: theme.palette.common.white,
  },
  scanning: {
    backgroundColor: theme.palette.info.main,
    color: theme.palette.common.white,
  },
  notScanned: {
    backgroundColor: theme.palette.grey[400],
    color: theme.palette.common.white,
  },
}));

export interface ApmeRepoStatusChipProps {
  repoUrl: string;
  branch?: string;
  projectDetailPath: string;
}

/** Violation status chip for Git Repository catalog rows. */
export const ApmeRepoStatusChip = ({
  repoUrl,
  branch,
  projectDetailPath,
}: ApmeRepoStatusChipProps) => {
  const classes = useStyles();
  const apmeApi = useApi(apmeApiRef);
  const navigate = useNavigate();
  const enabled = useApmeEnabled();
  const [project, setProject] = useState<Project | null>(null);
  const [loading, setLoading] = useState(true);
  const [operationActive, setOperationActive] = useState(false);

  useEffect(() => {
    if (!enabled || !repoUrl) {
      setProject(null);
      setOperationActive(false);
      setLoading(false);
      return undefined;
    }

    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    function schedule(ms: number) {
      timer = setTimeout(() => {
        void tick();
      }, ms);
    }

    async function tick() {
      try {
        const fresh = await apmeApi.getProjectByRepoUrl(repoUrl, branch);
        if (cancelled) {
          return;
        }

        let active = projectHasActiveOperation(fresh);
        // Only call operation/state when the project already looks in-flight.
        // Idle projects return Gateway 404 here — noisy and unnecessary.
        if (active && fresh?.id) {
          const state = await apmeApi.getOperationState(fresh.id);
          if (cancelled) {
            return;
          }
          active =
            shouldResumeScanUi(state) || projectHasActiveOperation(fresh);
        }

        setProject(fresh);
        setOperationActive(active);
        setLoading(false);
        schedule(active ? ACTIVE_POLL_MS : IDLE_POLL_MS);
      } catch {
        if (!cancelled) {
          setLoading(false);
          schedule(IDLE_POLL_MS);
        }
      }
    }

    setLoading(true);
    void tick();

    return () => {
      cancelled = true;
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    };
  }, [enabled, repoUrl, branch, apmeApi]);

  if (!enabled) {
    return null;
  }

  if (loading && !project) {
    return <CircularProgress size={16} />;
  }

  if (!project) {
    return (
      <Chip size="small" label="Not scanned" className={classes.notScanned} />
    );
  }

  if (operationActive || projectHasActiveOperation(project)) {
    return <Chip size="small" label="Scanning…" className={classes.scanning} />;
  }

  if (project.total_violations === 0) {
    return <Chip size="small" label="Clean" className={classes.clean} />;
  }

  const label = `${project.total_violations} violation${
    project.total_violations === 1 ? "" : "s"
  }`;

  const handleClick = () => {
    navigate(projectDetailPath);
  };

  const worst = projectWorstSeverity(project);
  if (!worst) {
    return (
      <Chip
        size="small"
        label={label}
        className={classes.notScanned}
        onClick={handleClick}
        clickable
      />
    );
  }

  return (
    <Chip
      size="small"
      label={label}
      style={chipStyleForSeverity(worst.level)}
      onClick={handleClick}
      clickable
    />
  );
};
