import { AuthorizeResult } from "@backstage/plugin-permission-common";
import { createRequireSettingsViewMiddleware } from "./requireSettingsView";

describe("createRequireSettingsViewMiddleware", () => {
  const mockCredentials = { principal: { type: "user" } };
  const mockHttpAuth = {
    credentials: jest.fn().mockResolvedValue(mockCredentials),
  };

  it("calls next() when permission is ALLOW", async () => {
    const mockPermissions = {
      authorize: jest
        .fn()
        .mockResolvedValue([{ result: AuthorizeResult.ALLOW }]),
    };
    const middleware = createRequireSettingsViewMiddleware({
      httpAuth: mockHttpAuth as never,
      permissions: mockPermissions as never,
    });
    const next = jest.fn();
    const res = { status: jest.fn().mockReturnThis(), json: jest.fn() };

    await middleware({}, res, next);

    expect(next).toHaveBeenCalled();
    expect(res.status).not.toHaveBeenCalled();
  });

  it("returns 403 when permission is DENY", async () => {
    const mockPermissions = {
      authorize: jest
        .fn()
        .mockResolvedValue([{ result: AuthorizeResult.DENY }]),
    };
    const middleware = createRequireSettingsViewMiddleware({
      httpAuth: mockHttpAuth as never,
      permissions: mockPermissions as never,
    });
    const next = jest.fn();
    const res = { status: jest.fn().mockReturnThis(), json: jest.fn() };

    await middleware({}, res, next);

    expect(next).not.toHaveBeenCalled();
    expect(res.status).toHaveBeenCalledWith(403);
    expect(res.json).toHaveBeenCalledWith(
      expect.objectContaining({
        error: expect.stringContaining("ansible.settings.view"),
      }),
    );
  });
});
