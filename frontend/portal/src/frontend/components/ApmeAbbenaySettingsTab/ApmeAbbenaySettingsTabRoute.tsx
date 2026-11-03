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

import { SettingsLayout } from "@backstage/plugin-user-settings";
import { useApmeEnabled } from "../../hooks/useApmeEnabled";
import { ApmeAbbenaySettingsTab } from "./ApmeAbbenaySettingsTab";

/** Monolith app: child of UserSettingsPage route (adds sidebar tab). */
export const ApmeAbbenaySettingsTabRoute = () => {
  const apmeEnabled = useApmeEnabled();
  if (!apmeEnabled) {
    return null;
  }
  return (
    <SettingsLayout.Route path="/abbenay-ai" title="Abbenay AI">
      <ApmeAbbenaySettingsTab />
    </SettingsLayout.Route>
  );
};
