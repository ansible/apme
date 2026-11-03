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

import { useCallback, useEffect, useState } from "react";
import { useApi } from "@backstage/core-plugin-api";
import Box from "@material-ui/core/Box";
import Button from "@material-ui/core/Button";
import Card from "@material-ui/core/Card";
import CardContent from "@material-ui/core/CardContent";
import CardHeader from "@material-ui/core/CardHeader";
import Chip from "@material-ui/core/Chip";
import Dialog from "@material-ui/core/Dialog";
import DialogActions from "@material-ui/core/DialogActions";
import DialogContent from "@material-ui/core/DialogContent";
import DialogTitle from "@material-ui/core/DialogTitle";
import Divider from "@material-ui/core/Divider";
import IconButton from "@material-ui/core/IconButton";
import List from "@material-ui/core/List";
import ListItem from "@material-ui/core/ListItem";
import ListItemSecondaryAction from "@material-ui/core/ListItemSecondaryAction";
import ListItemText from "@material-ui/core/ListItemText";
import Typography from "@material-ui/core/Typography";
import DeleteIcon from "@material-ui/icons/Delete";
import EditIcon from "@material-ui/icons/Edit";
import LockIcon from "@material-ui/icons/Lock";
import { makeStyles } from "@material-ui/core/styles";
import { Progress } from "@backstage/core-components";
import type {
  ApmeAiProviderSummary,
  ApmeAiProviderConfigureRequest,
  ApmeAiStatus,
  ApmeAiEngineInfo,
} from "../../../common/types";
import {
  mergeApmeAiProviderLists,
  normalizeApmeAiProviders,
} from "../../../common/types";
import { apmeApiRef } from "../../api";
import { persistApmeDefaultAiModel } from "../../hooks/useApmeWorkflowAiModel";
import { ApmeAiProviderDialog } from "./ApmeAiProviderDialog";

const useStyles = makeStyles((theme) => ({
  // MUI CardHeader action defaults to alignSelf:flex-start + marginTop:-4.
  cardHeader: {
    alignItems: "center",
    "& .MuiCardHeader-action": {
      marginTop: 0,
      marginRight: 0,
      alignSelf: "center",
    },
  },
  headerActions: {
    display: "flex",
    flexDirection: "row",
    alignItems: "center",
    gap: theme.spacing(1),
    "& > *": {
      marginTop: 0,
      marginBottom: 0,
    },
  },
  statusButton: {
    // Non-interactive status — same size="small" Button metrics as Add provider
    pointerEvents: "none",
    cursor: "default",
  },
  emptyState: {
    padding: theme.spacing(1, 0, 2),
  },
  emptyStateTitle: {
    fontWeight: 600,
    marginBottom: theme.spacing(0.5),
  },
  emptyText: {
    color: theme.palette.text.secondary,
  },
  sectionHint: {
    color: theme.palette.text.secondary,
    marginBottom: theme.spacing(1),
  },
  modelsHeading: {
    marginTop: theme.spacing(2),
    marginBottom: theme.spacing(0.5),
  },
  modelChipsRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: theme.spacing(1),
    marginTop: theme.spacing(0.5),
  },
  modelChips: {
    display: "flex",
    flexWrap: "wrap",
    gap: theme.spacing(0.5),
    flex: "1 1 auto",
    minWidth: 0,
  },
  errorText: {
    color: theme.palette.error.main,
    marginBottom: theme.spacing(1),
  },
  engineChip: {
    marginRight: theme.spacing(0.5),
  },
  sourceChip: {
    marginLeft: theme.spacing(1),
  },
  sectionTitle: {
    marginTop: theme.spacing(2),
    marginBottom: theme.spacing(0.5),
  },
  fillHeightCard: {
    flex: 1,
    width: "100%",
    height: "100%",
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
  },
  fillHeightContent: {
    flex: 1,
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    overflowY: "auto",
  },
  modelsSectionAtBottom: {
    marginTop: "auto",
    paddingTop: theme.spacing(2),
    flexShrink: 0,
  },
  emptyStateBottom: {
    marginTop: "auto",
    paddingTop: theme.spacing(2),
    borderTop: `1px solid ${theme.palette.divider}`,
    flexShrink: 0,
  },
  providerListItem: {
    paddingTop: theme.spacing(1),
    paddingBottom: theme.spacing(1),
    alignItems: "flex-start",
  },
  providerSecondary: {
    display: "block",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
    maxWidth: "100%",
  },
  expandAction: {
    marginTop: theme.spacing(1.5),
  },
  expandButton: {
    textTransform: "none",
    flexShrink: 0,
    marginLeft: "auto",
  },
}));

interface AiModelRow {
  id: string;
  provider: string;
  name: string;
}

/** Initial list count before "Show more" opens the full list modal (AAP-90214). */
const INITIAL_VISIBLE_PROVIDER_COUNT = 2;
const INITIAL_VISIBLE_MODEL_COUNT = 3;
const PREVIEW_MODEL_COUNT = 3;

function getNoProvidersAddedMessage(
  hasModels: boolean,
  hasConfigProviders: boolean,
): { title: string; description: string } {
  if (hasConfigProviders) {
    return {
      title: "No providers added",
      description:
        "No provider accounts have been added in the portal. System providers from your deployment ConfigMap are listed below and cannot be edited here. Use Add provider to configure a new AI provider account.",
    };
  }
  if (hasModels) {
    return {
      title: "No providers added",
      description:
        "No editable provider accounts are configured in the portal. Models listed below are read-only from Primary and available for scans. Use Add provider to add credentials and manage models here.",
    };
  }
  return {
    title: "No providers added",
    description:
      "Configure an AI provider to enable AI-assisted remediation in Quality workflows. Use Add provider to connect OpenAI, Anthropic, or another supported engine with your API credentials.",
  };
}

function formatProviderModelsSummary(models: string[]): string {
  if (models.length === 0) {
    return "No models configured";
  }
  const count = models.length;
  const label = `${count} model${count === 1 ? "" : "s"}`;
  if (count <= PREVIEW_MODEL_COUNT) {
    return `${label}: ${models.join(", ")}`;
  }
  const preview = models.slice(0, PREVIEW_MODEL_COUNT).join(", ");
  const remaining = count - PREVIEW_MODEL_COUNT;
  return `${label}: ${preview} +${remaining} more`;
}

interface ManagedProviderListProps {
  providers: ApmeAiProviderSummary[];
  classes: ReturnType<typeof useStyles>;
  removing: boolean;
  onEdit(provider: ApmeAiProviderSummary): void;
  onRemove(provider: ApmeAiProviderSummary): void;
}

const ManagedProviderList = ({
  providers,
  classes,
  removing,
  onEdit,
  onRemove,
}: ManagedProviderListProps) => (
  <List disablePadding>
    {providers.map((p, idx) => (
      <div key={p.id}>
        {idx > 0 && <Divider component="li" />}
        <ListItem disableGutters className={classes.providerListItem}>
          <ListItemText
            primary={
              <Box display="flex" alignItems="center">
                <Typography variant="body1" component="span">
                  {p.id}
                </Typography>
                <Chip
                  label={p.engine}
                  size="small"
                  className={classes.engineChip}
                  style={{ marginLeft: 8 }}
                />
              </Box>
            }
            secondary={
              <Typography
                variant="body2"
                component="span"
                className={classes.providerSecondary}
                title={p.models.join(", ")}
              >
                {formatProviderModelsSummary(p.models)}
              </Typography>
            }
          />
          <ListItemSecondaryAction>
            <IconButton
              edge="end"
              aria-label={`Edit provider ${p.id}`}
              size="small"
              onClick={() => onEdit(p)}
            >
              <EditIcon fontSize="small" />
            </IconButton>
            <IconButton
              edge="end"
              aria-label={`Remove provider ${p.id}`}
              size="small"
              onClick={() => onRemove(p)}
              disabled={removing}
            >
              <DeleteIcon fontSize="small" />
            </IconButton>
          </ListItemSecondaryAction>
        </ListItem>
      </div>
    ))}
  </List>
);

interface AllProvidersDialogProps {
  open: boolean;
  providers: ApmeAiProviderSummary[];
  removing: boolean;
  onClose(): void;
  onEdit(provider: ApmeAiProviderSummary): void;
  onRemove(provider: ApmeAiProviderSummary): void;
}

const AllProvidersDialog = ({
  open,
  providers,
  removing,
  onClose,
  onEdit,
  onRemove,
}: AllProvidersDialogProps) => {
  const classes = useStyles();

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="apme-all-providers-title"
    >
      <DialogTitle id="apme-all-providers-title">AI providers</DialogTitle>
      <DialogContent dividers>
        <ManagedProviderList
          providers={providers}
          classes={classes}
          removing={removing}
          onEdit={onEdit}
          onRemove={onRemove}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} color="primary">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

interface AvailableModelsDialogProps {
  open: boolean;
  models: AiModelRow[];
  onClose(): void;
}

const AvailableModelsDialog = ({
  open,
  models,
  onClose,
}: AvailableModelsDialogProps) => {
  const classes = useStyles();

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-labelledby="apme-available-models-title"
    >
      <DialogTitle id="apme-available-models-title">
        Available models
      </DialogTitle>
      <DialogContent dividers>
        <Box className={classes.modelChips}>
          {models.map((m) => (
            <Chip
              key={m.id}
              size="small"
              label={m.name || m.id}
              title={m.provider ? `${m.id} (${m.provider})` : m.id}
              variant="outlined"
            />
          ))}
        </Box>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} color="primary">
          Close
        </Button>
      </DialogActions>
    </Dialog>
  );
};

interface SupportedEnginesFooterProps {
  engines: ApmeAiEngineInfo[];
  classes: ReturnType<typeof useStyles>;
}

const SupportedEnginesFooter = ({
  engines,
  classes,
}: SupportedEnginesFooterProps) => {
  if (engines.length === 0) {
    return null;
  }

  return (
    <Box className={classes.emptyStateBottom}>
      <Typography variant="subtitle2">Supported engines</Typography>
      <Typography variant="body2" className={classes.sectionHint}>
        Engines available from your APME deployment.
      </Typography>
      <Box className={classes.modelChips}>
        {engines.map((engine) => (
          <Chip
            key={engine.id}
            size="small"
            label={engine.id}
            title={
              engine.requiresKey
                ? `${engine.id} (API key required)`
                : `${engine.id} (no API key required)`
            }
            variant="outlined"
          />
        ))}
      </Box>
    </Box>
  );
};

interface RemoveConfirmDialogProps {
  open: boolean;
  providerId: string;
  onCancel(): void;
  onConfirm(): void;
}

const RemoveConfirmDialog = ({
  open,
  providerId,
  onCancel,
  onConfirm,
}: RemoveConfirmDialogProps) => (
  <Dialog
    open={open}
    maxWidth="xs"
    fullWidth
    aria-labelledby="apme-remove-provider-title"
  >
    <DialogTitle id="apme-remove-provider-title">Remove provider</DialogTitle>
    <DialogContent dividers>
      <Typography>
        Remove provider <strong>{providerId}</strong>? This cannot be undone.
      </Typography>
    </DialogContent>
    <DialogActions>
      <Button onClick={onCancel} color="primary">
        Cancel
      </Button>
      <Button onClick={onConfirm} color="primary" variant="contained">
        Remove
      </Button>
    </DialogActions>
  </Dialog>
);

/**
 * AI providers card rendered beside the ansible-core card in Quality settings (US-016).
 */
export const ApmeAiProvidersSection = ({
  fillHeight = false,
}: {
  fillHeight?: boolean;
}) => {
  const classes = useStyles();
  const apmeApi = useApi(apmeApiRef);

  const [loading, setLoading] = useState(true);
  const [providers, setProviders] = useState<ApmeAiProviderSummary[]>([]);
  const [models, setModels] = useState<AiModelRow[]>([]);
  const [aiStatus, setAiStatus] = useState<ApmeAiStatus | undefined>();
  const [error, setError] = useState<string | undefined>();

  const [addOpen, setAddOpen] = useState(false);
  const [editProvider, setEditProvider] = useState<
    ApmeAiProviderSummary | undefined
  >();
  const [removeProvider, setRemoveProvider] = useState<
    ApmeAiProviderSummary | undefined
  >();
  const [removing, setRemoving] = useState(false);
  const [removeError, setRemoveError] = useState<string | undefined>();
  const [providersModalOpen, setProvidersModalOpen] = useState(false);
  const [modelsModalOpen, setModelsModalOpen] = useState(false);
  const [engines, setEngines] = useState<ApmeAiEngineInfo[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(undefined);
    try {
      const [prov, status, modelList, config, enginesRes] = await Promise.all([
        apmeApi.getAiProviders().catch(() => [] as ApmeAiProviderSummary[]),
        apmeApi.getAiStatus().catch(() => undefined),
        apmeApi.getAiModels().catch(() => [] as AiModelRow[]),
        apmeApi.getAiConfig().catch(() => undefined),
        apmeApi.getAiEngines().catch(() => ({ engines: [] })),
      ]);
      const configProviders =
        config !== undefined ? normalizeApmeAiProviders(config) : [];
      const nextProviders = mergeApmeAiProviderLists(prov, configProviders);
      setProviders(nextProviders);
      setAiStatus(status);
      setModels(modelList);
      setEngines(
        (enginesRes.engines ?? []).filter((engine) => engine.id !== "mock"),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [apmeApi]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleSave = async (
    id: string,
    payload: { configure: ApmeAiProviderConfigureRequest; models: string[] },
  ) => {
    const { configure, models: modelIds } = payload;
    await apmeApi.configureAiProvider(id, configure);
    // Merge models into Abbenay config via POST /api/config (separate endpoint).
    let raw: unknown;
    try {
      raw = await apmeApi.getAiConfig();
    } catch {
      throw new Error(
        "Provider saved but could not read AI config to persist models. Check Abbenay connectivity.",
      );
    }
    // Normalize: handle both {config:{providers}} and {providers} response shapes.
    const root =
      raw && typeof raw === "object" && "config" in raw && (raw as any).config
        ? (raw as any).config
        : raw;
    const config: Record<string, unknown> =
      root && typeof root === "object" ? { ...(root as object) } : {};
    const existingProviders = config.providers as
      Record<string, Record<string, unknown>> | undefined;
    const providersById: Record<
      string,
      Record<string, unknown>
    > = existingProviders ? { ...existingProviders } : {};
    const prev: Record<string, unknown> = providersById[id]
      ? { ...providersById[id] }
      : {};
    providersById[id] = {
      ...prev,
      engine: configure.engine || prev.engine,
      models: Object.fromEntries(modelIds.map((m) => [m, {}])),
    };
    if (configure.baseUrl) {
      providersById[id].base_url = configure.baseUrl;
    }
    await apmeApi.updateAiConfig({
      location: "user",
      config: { ...config, providers: providersById },
    });
    if (modelIds.length > 0) {
      await persistApmeDefaultAiModel(apmeApi, id, modelIds[0]);
    }
    await load();
  };

  const handleRemoveConfirm = async () => {
    if (!removeProvider) return;
    setRemoving(true);
    setRemoveError(undefined);
    try {
      await apmeApi.deleteAiProvider(removeProvider.id);
      setRemoveProvider(undefined);
      await load();
    } catch (err) {
      setRemoveError(err instanceof Error ? err.message : String(err));
    } finally {
      setRemoving(false);
    }
  };

  const statusLabel = (() => {
    if (!aiStatus) {
      return undefined;
    }
    return aiStatus.connected ? "Connected" : "Disconnected";
  })();

  const managedProviders = providers.filter((p) => p.source !== "config");
  const configProviders = providers.filter((p) => p.source === "config");
  const visibleManagedProviders = managedProviders.slice(
    0,
    INITIAL_VISIBLE_PROVIDER_COUNT,
  );
  const hiddenProviderCount = Math.max(
    0,
    managedProviders.length - INITIAL_VISIBLE_PROVIDER_COUNT,
  );
  const visibleModels = models.slice(0, INITIAL_VISIBLE_MODEL_COUNT);
  const hiddenModelCount = Math.max(
    0,
    models.length - INITIAL_VISIBLE_MODEL_COUNT,
  );
  const useStretchLayout = fillHeight;
  const noProvidersMessage = getNoProvidersAddedMessage(
    models.length > 0,
    configProviders.length > 0,
  );
  const showEmptyStateHelp =
    useStretchLayout && managedProviders.length === 0 && models.length === 0;

  const handleEditProvider = (provider: ApmeAiProviderSummary) => {
    setProvidersModalOpen(false);
    setEditProvider(provider);
  };

  const handleRemoveProvider = (provider: ApmeAiProviderSummary) => {
    setProvidersModalOpen(false);
    setRemoveError(undefined);
    setRemoveProvider(provider);
  };

  return (
    <>
      <Card className={useStretchLayout ? classes.fillHeightCard : undefined}>
        <CardHeader
          className={classes.cardHeader}
          title="AI providers"
          subheader="Provider accounts used for AI-assisted remediation"
          action={
            <Box className={classes.headerActions}>
              {statusLabel && (
                <Button
                  color={aiStatus?.connected ? "primary" : "default"}
                  variant="contained"
                  size="small"
                  disableElevation
                  disableRipple
                  tabIndex={-1}
                  aria-disabled="true"
                  className={classes.statusButton}
                >
                  {statusLabel}
                </Button>
              )}
              <Button
                color="primary"
                variant="outlined"
                size="small"
                onClick={() => setAddOpen(true)}
              >
                Add provider
              </Button>
            </Box>
          }
        />
        <CardContent
          className={useStretchLayout ? classes.fillHeightContent : undefined}
        >
          {error && (
            <Typography
              variant="body2"
              className={classes.errorText}
              role="alert"
            >
              {error}
            </Typography>
          )}
          {removeError && (
            <Typography
              variant="body2"
              className={classes.errorText}
              role="alert"
            >
              {removeError}
            </Typography>
          )}

          {loading && <Progress />}

          {!loading && managedProviders.length === 0 && (
            <Box className={classes.emptyState}>
              <Typography
                variant="subtitle1"
                className={classes.emptyStateTitle}
              >
                {noProvidersMessage.title}
              </Typography>
              <Typography variant="body2" className={classes.emptyText}>
                {noProvidersMessage.description}
              </Typography>
            </Box>
          )}

          {!loading && managedProviders.length > 0 && (
            <Box>
              <ManagedProviderList
                providers={visibleManagedProviders}
                classes={classes}
                removing={removing}
                onEdit={handleEditProvider}
                onRemove={handleRemoveProvider}
              />
              {hiddenProviderCount > 0 ? (
                <Box className={classes.expandAction}>
                  <Button
                    size="small"
                    variant="outlined"
                    color="primary"
                    className={classes.expandButton}
                    onClick={() => setProvidersModalOpen(true)}
                    aria-label={`View all ${managedProviders.length} AI providers`}
                  >
                    View all providers ({hiddenProviderCount} more)
                  </Button>
                </Box>
              ) : null}
            </Box>
          )}

          {!loading && configProviders.length > 0 && (
            <Box>
              <Typography variant="subtitle2" className={classes.sectionTitle}>
                System providers
              </Typography>
              <Typography variant="body2" className={classes.sectionHint}>
                Read-only providers from deploy-time ConfigMap. Edit/Delete are
                disabled — change them in your deployment YAML.
              </Typography>
              <List disablePadding>
                {configProviders.map((p, idx) => (
                  <div key={p.id}>
                    {idx > 0 && <Divider component="li" />}
                    <ListItem
                      disableGutters
                      className={classes.providerListItem}
                    >
                      <ListItemText
                        primary={
                          <Box display="flex" alignItems="center">
                            <LockIcon
                              fontSize="small"
                              style={{ marginRight: 8, opacity: 0.7 }}
                              aria-hidden
                            />
                            <Typography variant="body1" component="span">
                              {p.id}
                            </Typography>
                            <Chip
                              label={p.engine}
                              size="small"
                              className={classes.engineChip}
                              style={{ marginLeft: 8 }}
                            />
                            <Chip
                              label="Source: ConfigMap"
                              size="small"
                              variant="outlined"
                              className={classes.sourceChip}
                            />
                          </Box>
                        }
                        secondary={
                          <Typography
                            variant="body2"
                            component="span"
                            className={classes.providerSecondary}
                            title={p.models.join(", ")}
                          >
                            {formatProviderModelsSummary(p.models)}
                          </Typography>
                        }
                      />
                    </ListItem>
                  </div>
                ))}
              </List>
            </Box>
          )}

          {!loading && models.length > 0 && (
            <Box
              className={
                useStretchLayout ? classes.modelsSectionAtBottom : undefined
              }
            >
              <Typography variant="subtitle2" className={classes.modelsHeading}>
                Available models
              </Typography>
              <Typography variant="body2" className={classes.sectionHint}>
                Read-only list from Primary (same source as the Quality tab AI
                toggle). Edit providers above to change available models.
              </Typography>
              <Box className={classes.modelChipsRow}>
                <Box className={classes.modelChips}>
                  {visibleModels.map((m) => (
                    <Chip
                      key={m.id}
                      size="small"
                      label={m.name || m.id}
                      title={m.provider ? `${m.id} (${m.provider})` : m.id}
                      variant="outlined"
                    />
                  ))}
                </Box>
                {hiddenModelCount > 0 ? (
                  <Button
                    size="small"
                    variant="outlined"
                    color="primary"
                    className={classes.expandButton}
                    onClick={() => setModelsModalOpen(true)}
                    aria-label={`View all ${models.length} available models`}
                  >
                    View all models ({hiddenModelCount} more)
                  </Button>
                ) : null}
              </Box>
            </Box>
          )}

          {!loading && showEmptyStateHelp && (
            <SupportedEnginesFooter engines={engines} classes={classes} />
          )}
        </CardContent>
      </Card>

      <ApmeAiProviderDialog
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSave={handleSave}
      />

      {editProvider && (
        <ApmeAiProviderDialog
          open
          provider={editProvider}
          onClose={() => setEditProvider(undefined)}
          onSave={handleSave}
        />
      )}

      {removeProvider && (
        <RemoveConfirmDialog
          open
          providerId={removeProvider.id}
          onCancel={() => setRemoveProvider(undefined)}
          onConfirm={() => void handleRemoveConfirm()}
        />
      )}

      <AllProvidersDialog
        open={providersModalOpen}
        providers={managedProviders}
        removing={removing}
        onClose={() => setProvidersModalOpen(false)}
        onEdit={handleEditProvider}
        onRemove={handleRemoveProvider}
      />

      <AvailableModelsDialog
        open={modelsModalOpen}
        models={models}
        onClose={() => setModelsModalOpen(false)}
      />
    </>
  );
};
