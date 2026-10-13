/* Terms of Service (/terms). Public, no auth required. Content is grounded in this
   codebase's actual implementation (plans, limits, cancellation behavior, GA4
   integration). This is a careful draft for the business owner's legal review, not
   a substitute for one — several clauses (refund policy, governing law, legal
   entity details) are intentionally left general pending that review; see the
   completion report for the specific open items. */
import React from "react";
import LegalPage, { Section } from "./LegalPage.jsx";

const SUPPORT_EMAIL = "aeomirror.support@gmail.com";
const EFFECTIVE_DATE = "October 13, 2026";

export default function TermsOfService() {
  return (
    <LegalPage
      title="Terms of Service"
      path="/terms"
      effectiveDate={EFFECTIVE_DATE}
      intro={
        <>
          These Terms govern your use of AEOMirror. By creating an account or using
          AEOMirror, you agree to these Terms. This is a plain-language draft — if
          anything here is unclear, contact us at{" "}
          <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a> before relying on it.
        </>
      }
    >
      <Section title="1. Acceptance and eligibility">
        <p>You must be able to form a binding contract to use AEOMirror, and you must
          provide accurate information when creating an account. If you create an
          account on behalf of an organization, you confirm you have the authority to
          bind that organization to these Terms.</p>
      </Section>

      <Section title="2. Your account and responsibilities">
        <p>You are responsible for keeping your login credentials secure and for all
          activity that happens under your account. Tell us promptly at{" "}
          <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a> if you suspect
          unauthorized access. You're responsible for the accuracy of the website
          URLs and other information you submit to AEOMirror, and for having the
          right to request a scan or connect an analytics property for any site you
          add.</p>
      </Section>

      <Section title="3. Permitted and prohibited use">
        <p>You may use AEOMirror to scan, analyze, and monitor websites you own, operate,
          or are otherwise authorized to assess. You agree not to: use AEOMirror to
          scan sites you have no right to assess; attempt to bypass rate limits, scan
          quotas, or access controls; interfere with or disrupt AEOMirror's
          infrastructure; reverse-engineer or resell the service without our consent;
          or use the product to violate any applicable law.</p>
      </Section>

      <Section title="4. Scans, scores, and recommendations">
        <p>AEOMirror's AI visibility scores, signal checks, recommendations, AI-written
          report narratives, and Answer Tracking results are automated, point-in-time
          estimates based on publicly observable signals and, where applicable,
          responses from third-party AI providers. <strong>They are estimates, not
          guarantees.</strong> AEOMirror does not guarantee any specific search
          ranking, AI assistant citation, increase in traffic, or revenue outcome as a
          result of following our recommendations or using the product.</p>
      </Section>

      <Section title="5. Google Analytics connection">
        <p>If you choose to connect a Google Analytics 4 property, you confirm that you
          are authorized to grant AEOMirror read-only access to that property's data.
          You are responsible for disconnecting the integration if your access to that
          property changes or ends. See our{" "}
          <a href="/privacy">Privacy Policy</a> for exactly what we access, store, and
          how AI-referral traffic is classified, including its known limitations
          (we never claim to precisely identify Google AI Overview clicks).</p>
      </Section>

      <Section title="6. Plans, billing, and cancellation">
        <p>AEOMirror offers a free plan with limited monthly scans and monitors, a
          one-time report purchase that unlocks a single scan's full export, and a
          paid Pro subscription with higher limits and additional features. Paid
          subscriptions are billed through our payment processor, Stripe, on a
          recurring monthly basis until cancelled.</p>
        <p>You can cancel your Pro subscription at any time from your billing settings.
          Cancellation takes effect at the end of your current billing period — you
          keep Pro access through the period you've already paid for, and you will
          not be charged again afterward. You can resume a pending cancellation before
          the period ends.</p>
        <p><strong>Refunds:</strong> refund requests are reviewed on a case-by-case
          basis — contact <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>. We
          do not currently guarantee automatic refunds for partial billing periods.</p>
      </Section>

      <Section title="7. Intellectual property">
        <p>You retain ownership of the website content and URLs you submit to AEOMirror.
          You grant us permission to process that content solely to provide the
          product to you (running scans, generating reports, and the AI-processing
          features described in our Privacy Policy). The reports, scores, and
          recommendations AEOMirror generates for your account are yours to use for
          your own business purposes. AEOMirror and its underlying software, design,
          and branding remain our property.</p>
      </Section>

      <Section title="8. Third-party services">
        <p>AEOMirror relies on third-party infrastructure and AI providers described in
          our <a href="/privacy">Privacy Policy</a>. We are not responsible for
          outages, errors, or changes made by those third parties, though we aim to
          degrade gracefully (for example, AI-dependent features fall back to
          deterministic analysis if an AI provider is unavailable).</p>
      </Section>

      <Section title="9. Availability, suspension, and termination">
        <p>We aim to keep AEOMirror available but do not guarantee uninterrupted access
          — the service may be unavailable for maintenance, updates, or circumstances
          outside our control. We may suspend or terminate an account that violates
          these Terms, including the prohibited-use rules above. You may stop using
          AEOMirror at any time; to request deletion of your account and data, contact{" "}
          <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>.</p>
      </Section>

      <Section title="10. Disclaimer of warranties">
        <p>AEOMirror is provided "as is" and "as available," without warranties of any
          kind, whether express or implied, including — to the extent permitted by
          applicable law — any implied warranty of merchantability, fitness for a
          particular purpose, or non-infringement. We do not warrant that the service
          will be error-free, uninterrupted, or that scores and recommendations will
          be accurate or complete.</p>
      </Section>

      <Section title="11. Limitation of liability">
        <p>To the fullest extent permitted by applicable law, AEOMirror and its
          operators will not be liable for any indirect, incidental, special,
          consequential, or punitive damages, or any loss of revenue, profits, or
          data, arising from your use of the service. This section is drafted broadly
          as a starting point and should be reviewed against the laws that actually
          apply to AEOMirror's business before being treated as final.</p>
      </Section>

      <Section title="12. Changes to these Terms">
        <p>We may update these Terms as AEOMirror's product changes. We will update the
          "Last updated" date above when we do. Continued use of AEOMirror after a
          change means you accept the updated Terms.</p>
      </Section>

      <Section title="13. Governing law and contact">
        <p>These Terms are governed by the law applicable to AEOMirror's operations;
          the specific governing jurisdiction will be confirmed here once finalized by
          AEOMirror. Questions about these Terms can be sent to{" "}
          <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>, or via our{" "}
          <a href="/contact">contact page</a>.</p>
      </Section>
    </LegalPage>
  );
}
