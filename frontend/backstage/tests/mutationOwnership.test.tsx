import { Suspense, startTransition, useState } from "react";
import { fireEvent, render, screen, act } from "@testing-library/react";
import { useMutation } from "../plugins/backstage-apme/src/components/useMutation";

const suspended = new Promise<void>(() => {});

function Harness({
  action,
  onSaved,
}: {
  action: () => Promise<void>;
  onSaved: () => void;
}) {
  const [scope, setScope] = useState("current");
  const mutation = useMutation(scope);
  if (scope === "next") throw suspended;
  return (
    <>
      <button onClick={() => startTransition(() => setScope("next"))}>
        Change scope
      </button>
      <button onClick={() => void mutation.run(action, onSaved)}>Save</button>
    </>
  );
}

it("keeps callbacks owned by the committed scope after a suspended render", async () => {
  let finishAction!: () => void;
  const action = () => new Promise<void>((resolve) => (finishAction = resolve));
  const onSaved = vi.fn();
  render(
    <Suspense fallback={<div>Loading new scope</div>}>
      <Harness action={action} onSaved={onSaved} />
    </Suspense>,
  );

  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  fireEvent.click(screen.getByRole("button", { name: "Change scope" }));
  expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();

  await act(async () => finishAction());
  expect(onSaved).toHaveBeenCalledOnce();
});
