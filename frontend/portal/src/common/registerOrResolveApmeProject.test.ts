/*
 * Copyright Red Hat
 */

import {
  isApmeProjectConflictError,
  registerOrResolveApmeProject,
  resolveApmeProject,
  type ApmeProjectResolver,
} from "./registerOrResolveApmeProject";

const baseProject = {
  id: "proj-1",
  name: "ans-tower-devsecops",
  repo_url: "https://github.com/acme/ans-tower-devsecops",
  branch: "main",
  scan_count: 0,
  total_violations: 0,
};

function mockService(
  overrides: Partial<ApmeProjectResolver> = {},
): ApmeProjectResolver {
  return {
    getProjectByRepoUrl: jest.fn().mockResolvedValue(null),
    getProjects: jest.fn().mockResolvedValue([baseProject]),
    createProject: jest.fn(),
    ...overrides,
  };
}

describe("isApmeProjectConflictError", () => {
  it("detects 409 already-exists errors", () => {
    expect(
      isApmeProjectConflictError(
        new Error('APME API conflict: {"detail":"already exists"}'),
      ),
    ).toBe(true);
  });

  it("ignores non-error values", () => {
    expect(isApmeProjectConflictError("already exists")).toBe(false);
  });
});

describe("resolveApmeProject", () => {
  it("returns early when getProjectByRepoUrl hits", async () => {
    const byRepo = { ...baseProject, id: "from-lookup" };
    const apmeService = mockService({
      getProjectByRepoUrl: jest.fn().mockResolvedValue(byRepo),
    });

    const project = await resolveApmeProject(
      apmeService,
      "https://github.com/acme/ans-tower-devsecops",
      "main",
    );

    expect(project).toEqual(byRepo);
    expect(apmeService.getProjects).not.toHaveBeenCalled();
  });

  it("falls back to project list lookup when repo lookup misses", async () => {
    const apmeService = mockService({});
    const project = await resolveApmeProject(
      apmeService,
      "https://github.com/acme/ans-tower-devsecops.git",
      "main",
    );
    expect(project).toEqual(baseProject);
    expect(apmeService.getProjects).toHaveBeenCalled();
  });

  it("returns null when no project matches", async () => {
    const apmeService = mockService({
      getProjects: jest.fn().mockResolvedValue([]),
    });
    await expect(
      resolveApmeProject(
        apmeService,
        "https://github.com/acme/missing",
        "main",
      ),
    ).resolves.toBeNull();
  });

  it("does not resolve a different repository by a shared display name", async () => {
    const apmeService = mockService({
      getProjects: jest.fn().mockResolvedValue([baseProject]),
    });

    await expect(
      resolveApmeProject(
        apmeService,
        "https://github.com/other/ans-tower-devsecops",
        "main",
      ),
    ).resolves.toBeNull();
  });
});

describe("registerOrResolveApmeProject", () => {
  it("returns existing project before create when list lookup succeeds", async () => {
    const apmeService = mockService({});

    const project = await registerOrResolveApmeProject(apmeService, {
      name: "ans-tower-devsecops",
      repo_url: "https://github.com/acme/ans-tower-devsecops",
      branch: "main",
    });

    expect(project).toEqual(baseProject);
    expect(apmeService.createProject).not.toHaveBeenCalled();
  });

  it("creates a project when resolve misses", async () => {
    const created = { ...baseProject, id: "created-1" };
    const apmeService = mockService({
      getProjects: jest.fn().mockResolvedValue([]),
      createProject: jest.fn().mockResolvedValue(created),
    });

    const project = await registerOrResolveApmeProject(apmeService, {
      name: "ans-tower-devsecops",
      repo_url: "https://github.com/acme/ans-tower-devsecops",
      branch: "main",
    });

    expect(project).toEqual(created);
    expect(apmeService.createProject).toHaveBeenCalled();
  });

  it("rethrows non-conflict create errors", async () => {
    const apmeService = mockService({
      getProjects: jest.fn().mockResolvedValue([]),
      createProject: jest
        .fn()
        .mockRejectedValue(new Error("APME API error: 500 - boom")),
    });

    await expect(
      registerOrResolveApmeProject(apmeService, {
        name: "ans-tower-devsecops",
        repo_url: "https://github.com/acme/ans-tower-devsecops",
        branch: "main",
      }),
    ).rejects.toThrow(/500/);
  });

  it("returns existing project when create conflicts", async () => {
    const apmeService = mockService({
      getProjects: jest
        .fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([baseProject]),
      createProject: jest
        .fn()
        .mockRejectedValue(
          new Error(
            'APME API conflict: {"detail":"Project named \'ans-tower-devsecops\' already exists"}',
          ),
        ),
    });

    const project = await registerOrResolveApmeProject(apmeService, {
      name: "ans-tower-devsecops",
      repo_url: "https://github.com/acme/ans-tower-devsecops",
      branch: "main",
    });

    expect(project).toEqual(baseProject);
    expect(apmeService.createProject).toHaveBeenCalled();
  });

  it("rethrows conflict when resolve still returns null", async () => {
    const conflict = new Error(
      'APME API conflict: {"detail":"already exists"}',
    );
    const apmeService = mockService({
      getProjects: jest.fn().mockResolvedValue([]),
      createProject: jest.fn().mockRejectedValue(conflict),
    });

    await expect(
      registerOrResolveApmeProject(apmeService, {
        name: "ans-tower-devsecops",
        repo_url: "https://github.com/acme/ans-tower-devsecops",
        branch: "main",
      }),
    ).rejects.toBe(conflict);
  });

  it("does not reuse a same-name project after a create conflict", async () => {
    const conflict = new Error(
      'APME API conflict: {"detail":"already exists"}',
    );
    const apmeService = mockService({
      getProjects: jest
        .fn()
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([baseProject]),
      createProject: jest.fn().mockRejectedValue(conflict),
    });

    await expect(
      registerOrResolveApmeProject(apmeService, {
        name: "ans-tower-devsecops",
        repo_url: "https://github.com/other/ans-tower-devsecops",
        branch: "main",
      }),
    ).rejects.toBe(conflict);
  });
});
