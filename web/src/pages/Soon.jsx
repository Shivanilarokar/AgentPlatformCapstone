/* Screens that are wired into the nav but not built yet.
 *
 * Deliberately honest: it says which step builds it rather than pretending to
 * be a real screen with fake data in it.
 */

import { TopBar } from "../ui";

export default function Soon({ title, sub, step, what }) {
  return (
    <>
      <TopBar title={title} sub={sub} />
      <div className="content">
        <div className="note">
          <b>Not built yet — step {step}.</b> {what}
        </div>
        <p className="muted" style={{ marginTop: 16, fontSize: 13, maxWidth: "60ch" }}>
          This screen is in the navigation because the shape of the product is fixed. It stays
          empty until the machinery behind it works, rather than showing numbers that are not real.
        </p>
      </div>
    </>
  );
}
