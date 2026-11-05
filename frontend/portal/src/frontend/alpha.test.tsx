import "@testing-library/jest-dom";
import { fireEvent, screen } from "@testing-library/react";
import { useApi } from "@backstage/core-plugin-api";
import { useMemo } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { compatWrapper } from "@backstage/core-compat-api";
import {
  createFrontendPlugin,
  createRouteRef,
  PageBlueprint,
} from "@backstage/frontend-plugin-api";
import { mockApis, renderTestApp } from "@backstage/frontend-test-utils";
import {
  createPortalEntityPage,
  createPortalContributionProvider,
  PortalEntitySurface,
  createPortalScaffolderPage,
  NativeScaffolderFields,
  NativeCatalogHeaderActions,
  useNativeCatalogTabs,
  useNativeCatalogColumns,
} from "@ansible/portal-extension-host/alpha";
import { ExtensionTabContent } from "@ansible/portal-extension-host";
import {
  contributionRegistry,
  portalOperationsApiRef,
  portalEntitlementsApiRef,
} from "@ansible/portal-extension-api";
import apmePlugin from "./alpha";
import { apmeApiRef } from "./api/ApmeApi";

function ApiProbe() {
  const api = useApi(apmeApiRef);
  return (
    <div>
      {typeof api.getHealth === "function"
        ? "APME API registered"
        : "Missing API"}
    </div>
  );
}

jest.mock("./components/EntityQualityTab", () => ({
  EntityQualityTab: ({
    entity,
    initialRuleFilter,
    initialCategoryFilter,
  }: {
    entity: { metadata: { name: string } };
    initialRuleFilter?: string;
    initialCategoryFilter?: string;
  }) => (
    <>
      <div>APME Quality: {entity.metadata.name}</div>
      <div>
        Filters: {initialRuleFilter}/{initialCategoryFilter}
      </div>
    </>
  ),
}));
jest.mock("./components/FleetQualityTab", () => ({
  FleetQualityTab: ({
    repositoryDetailPath,
  }: {
    repositoryDetailPath: (name: string, rule?: string) => string;
  }) => <a href={repositoryDetailPath("repo", "R001")}>Fleet repository</a>,
}));
jest.mock("./components/ApmeQualitySettingsTab", () => ({
  ApmeQualitySettingsTab: () => <div>Native quality settings</div>,
}));
jest.mock("./components/ApmeRulesTab", () => ({
  ApmeRulesTab: () => <div>Native rules</div>,
}));
jest.mock("./apis/apmeViolationsCell", () => ({
  ApmeViolationsCell: ({
    entity,
  }: {
    entity: { metadata: { name: string } };
  }) => <div>Native violations: {entity.metadata.name}</div>,
}));
function CatalogProbe() {
  const context = useMemo(
    () => ({
      contentType: "playbook-repository",
      entityDetailPath: (
        name: string,
        tab?: string,
        filters?: Record<string, string>,
      ) => `/host/${name}?tab=${tab}&rule=${filters?.rule}`,
    }),
    [],
  );
  const tabs = useNativeCatalogTabs(context);
  const columns = useNativeCatalogColumns("playbook-repository");
  return (
    <>
      {columns.map((column) => (
        <div key={column.id}>
          {column.title}
          {column.render({
            apiVersion: "backstage.io/v1alpha1",
            kind: "Component",
            metadata: {
              name: "repo",
              annotations: { "github.com/project-slug": "acme/repo" },
            },
            spec: { type: "git-repository" },
          })}
        </div>
      ))}
      {tabs?.map((tab) => (
        <div key={tab.id}>
          {tab.label}
          <ExtensionTabContent contribution={tab} />
        </div>
      ))}
    </>
  );
}

function QualityDestinationProbe() {
  const location = useLocation();
  const [search] = useSearchParams();
  const context = useMemo(
    () => ({
      contentType: "playbook-repository",
      entityDetailPath: (name: string) => `/host/${name}`,
    }),
    [],
  );
  const tabs = useNativeCatalogTabs(context);
  const tab = tabs?.find((item) => item.id === search.get("catalogTab"));
  return (
    <>
      <div>
        Destination: {location.pathname}
        {location.search}
      </div>
      {tab && <ExtensionTabContent contribution={tab} />}
    </>
  );
}
jest.mock("./components/EntityQualityActivityTab", () => ({
  EntityQualityActivityTab: ({
    entity,
  }: {
    entity: { metadata: { name: string } };
  }) => <div>APME activity: {entity.metadata.name}</div>,
}));
jest.mock("./components/DependenciesTab/DependenciesTab", () => ({
  DependenciesTab: ({
    context,
  }: {
    context: { entity: { metadata: { name: string } } };
  }) => <div>APME dependencies: {context.entity.metadata.name}</div>,
}));
jest.mock(
  "./components/ApmeRepositoryOverviewCard/ApmeRepositoryOverviewCard",
  () => ({
    ApmeRepositoryOverviewCard: ({
      context,
      onOpenQuality,
      navigationDisabled,
    }: {
      context: { entity: { metadata: { name: string } } };
      onOpenQuality?: () => void;
      navigationDisabled?: boolean;
    }) => (
      <button disabled={navigationDisabled} onClick={onOpenQuality}>
        APME summary: {context.entity.metadata.name}
      </button>
    ),
  }),
);

describe("APME NFS entry point", () => {
  const entitledApis = () => [
    mockApis.permission(),
    mockApis.identity(),
    [
      portalEntitlementsApiRef,
      { check: async () => ({ allowed: true }) },
    ] as const,
  ];
  const qualityRouteFeatures = () => [
    createFrontendPlugin({
      pluginId: "portal",
      extensions: [
        createPortalContributionProvider(),
        PageBlueprint.make({
          name: "repositories",
          params: {
            path: "/self-service/repositories",
            loader: async () => compatWrapper(<QualityDestinationProbe />),
          },
        }),
      ],
    }),
    apmePlugin,
  ];

  it("discovers the Content Quality route and selects the existing fleet tab", async () => {
    const before = contributionRegistry.getManifests();
    await renderTestApp({
      features: qualityRouteFeatures(),
      config: {
        backend: { baseUrl: "http://localhost:7007" },
        ansible: { apme: { enabled: true, mockMode: true } },
      },
      initialRouteEntries: ["/self-service/content-quality"],
      apis: entitledApis(),
    });
    expect(
      await screen.findByText(
        "Destination: /self-service/repositories/catalog?catalogTab=apme.quality-assessment.fleet-quality-tab",
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("link", { name: "Fleet repository" }),
    ).toBeInTheDocument();
    expect(contributionRegistry.getManifests()).toEqual(before);
  });

  it.each([false, undefined])(
    "does not redirect to Quality when enablement is %s",
    async (enabled) => {
      await renderTestApp({
        features: qualityRouteFeatures(),
        config: {
          backend: { baseUrl: "http://localhost:7007" },
          ansible: {
            apme: {
              ...(enabled === undefined ? {} : { enabled }),
              mockMode: true,
            },
          },
        },
        initialRouteEntries: ["/self-service/content-quality"],
        apis: [mockApis.permission()],
      });
      expect(await screen.findByRole("status")).toHaveTextContent(
        "Content Quality is unavailable because APME is disabled.",
      );
      expect(screen.queryByText(/Destination:/)).not.toBeInTheDocument();
    },
  );

  it("discovers fleet, settings and rules without legacy registration", async () => {
    const before = contributionRegistry.getManifests();
    await renderTestApp({
      features: [
        createFrontendPlugin({
          pluginId: "portal",
          extensions: [
            createPortalContributionProvider(),
            PageBlueprint.make({
              name: "catalog",
              params: {
                path: "/catalog",
                loader: async () => compatWrapper(<CatalogProbe />),
              },
            }),
          ],
        }),
        apmePlugin,
      ],
      config: {
        backend: { baseUrl: "http://localhost:7007" },
        ansible: { apme: { enabled: true, mockMode: true } },
      },
      initialRouteEntries: ["/catalog"],
      apis: entitledApis(),
    });
    expect(await screen.findByText("Native quality settings")).toBeTruthy();
    expect(await screen.findByText("Native rules")).toBeTruthy();
    expect(await screen.findByText("Native violations: repo")).toBeTruthy();
    expect(
      await screen.findByRole("link", { name: "Fleet repository" }),
    ).toHaveAttribute(
      "href",
      "/host/repo?tab=apme.quality-assessment.quality-tab&rule=R001",
    );
    expect(contributionRegistry.getManifests()).toEqual(before);
  });
  it("contributes its real URL-normalizing widget through native discovery", async () => {
    const onChange = jest.fn();
    const page = createPortalScaffolderPage({
      path: "/native-fields",
      routeRef: createRouteRef(),
      loader: async (declarations) =>
        compatWrapper(
          <NativeScaffolderFields declarations={declarations}>
            {(fields) => {
              const field = fields.find(
                (item) => item.name === "GitHubRepoUrlField",
              );
              if (!field) return <div>Field unavailable</div>;
              const Widget = field.component;
              return (
                <Widget
                  {...({
                    onChange,
                    formData: "",
                    schema: { type: "string" },
                    uiSchema: {},
                    required: true,
                    idSchema: { $id: "repoUrl" },
                  } as any)}
                />
              );
            }}
          </NativeScaffolderFields>,
        ),
    });
    await renderTestApp({
      features: [
        createFrontendPlugin({
          pluginId: "portal",
          extensions: [createPortalContributionProvider()],
        }),
        createFrontendPlugin({
          pluginId: "portal-scaffolder",
          extensions: [page],
        }),
        apmePlugin,
      ],
      config: {
        backend: { baseUrl: "http://localhost:7007" },
        ansible: { apme: { enabled: true, mockMode: true } },
      },
      initialRouteEntries: ["/native-fields"],
      apis: [mockApis.permission()],
    });
    const input = await screen.findByLabelText("GitHub repository URL");
    fireEvent.change(input, {
      target: {
        value: "https://github.com/test-rhaap-portal-3/test-amazon-aws.git",
      },
    });
    expect(onChange).toHaveBeenLastCalledWith(
      "github.com?owner=test-rhaap-portal-3&repo=test-amazon-aws",
    );
    expect(contributionRegistry.getScaffolderFields()).toEqual([]);
  });
  it("is discovered by the host without importing the legacy plugin entry point", async () => {
    const before = contributionRegistry.getManifests();
    const execute = jest
      .fn()
      .mockResolvedValue({ result: { scanId: "test-scan" } });
    const page = createPortalEntityPage({
      path: "/entity",
      loader: async (tabs, actions, cards) =>
        compatWrapper(
          <>
            <ApiProbe />
            <NativeCatalogHeaderActions
              contentType="playbook-repository"
              templatePath={(namespace, name) =>
                `/self-service/create/templates/${namespace}/${name}`
              }
            />
            <PortalEntitySurface
              tabs={tabs}
              actions={actions}
              cards={cards}
              context={{
                entity: {
                  apiVersion: "backstage.io/v1alpha1",
                  kind: "Component",
                  metadata: {
                    name: "my-repo",
                    annotations: { "github.com/project-slug": "acme/my-repo" },
                  },
                  spec: { type: "git-repository" },
                },
                contentType: "playbook-repository",
              }}
              checkEntitlement={async () => true}
            />
          </>,
        ),
    });
    renderTestApp({
      features: [
        createFrontendPlugin({
          pluginId: "portal",
          extensions: [createPortalContributionProvider(), page],
        }),
        apmePlugin,
      ],
      config: {
        backend: { baseUrl: "http://localhost:7007" },
        ansible: { apme: { enabled: true, mockMode: true } },
      },
      initialRouteEntries: ["/entity?rule=R001&category=quality"],
      apis: [[portalOperationsApiRef, { execute }], mockApis.permission()],
    });
    expect(await screen.findByText("APME Quality: my-repo")).toBeTruthy();
    expect(await screen.findByText("Filters: R001/quality")).toBeTruthy();
    expect(screen.getByText("APME API registered")).toBeTruthy();
    expect(
      await screen.findByRole("button", { name: "Add repository" }),
    ).toHaveAttribute(
      "href",
      "/self-service/create/templates/default/apme-register-git-repository",
    );
    fireEvent.click(
      await screen.findByRole("tab", { name: "Quality activity" }),
    );
    expect(await screen.findByText("APME activity: my-repo")).toBeTruthy();
    fireEvent.click(await screen.findByRole("tab", { name: "Dependencies" }));
    expect(await screen.findByText("APME dependencies: my-repo")).toBeTruthy();
    fireEvent.click(
      await screen.findByRole("button", { name: "APME summary: my-repo" }),
    );
    expect(screen.getByRole("tab", { name: "Quality" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(execute).not.toHaveBeenCalled();
    fireEvent.click(
      await screen.findByRole("button", { name: "Scan for quality issues" }),
    );
    expect(await screen.findByRole("status")).toBeTruthy();
    expect(execute).toHaveBeenCalledWith("apme.quality.scan", {
      subject: { entityRef: "component:default/my-repo" },
    });
    expect(contributionRegistry.getManifests()).toEqual(before);
    expect(
      contributionRegistry.getManifests().find((item) => item.id === "apme"),
    ).toBeUndefined();
  });
});
