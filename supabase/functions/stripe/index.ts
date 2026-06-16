import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "npm:@supabase/supabase-js@2";
import Stripe from "npm:stripe@14.21.0";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Info, Apikey",
};
const ok = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { ...corsHeaders, "Content-Type": "application/json" } });
const err = (msg: string, status = 400) =>
  new Response(JSON.stringify({ error: msg }), { status, headers: { ...corsHeaders, "Content-Type": "application/json" } });

function sb() {
  return createClient(Deno.env.get("SUPABASE_URL") ?? "", Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "");
}
async function getUser(req: Request, supabase: ReturnType<typeof sb>) {
  const token = req.headers.get("Authorization")?.replace("Bearer ", "").trim();
  if (!token) return null;
  const { data } = await supabase
    .from("user_sessions")
    .select("users(*)")
    .eq("session_token", token)
    .gt("expires_at", new Date().toISOString())
    .maybeSingle();
  return data ? (data.users as Record<string, unknown>) : null;
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") return new Response(null, { status: 200, headers: corsHeaders });

  const url = new URL(req.url);
  const parts = url.pathname.split("/").filter(Boolean);
  const fnIdx = parts.indexOf("stripe");
  const sub = parts[fnIdx + 1] ?? "";
  const supabase = sb();
  const stripeKey = Deno.env.get("STRIPE_SECRET_KEY") ?? "";
  const stripe = new Stripe(stripeKey, { apiVersion: "2024-04-10" });
  const frontendUrl = Deno.env.get("FRONTEND_URL") ?? "http://localhost:3000";

  try {
    // GET /stripe/subscription — subscription status
    if (req.method === "GET" && sub === "subscription") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      const subscription = (user.subscription as Record<string, unknown>) ?? {};
      const isActive = subscription.status === "active" || subscription.status === "trialing";
      return ok({ is_pro: isActive, status: subscription.status ?? "free", plan: subscription.plan ?? "free", ...subscription });
    }

    // POST /stripe/checkout — create checkout session
    if (req.method === "POST" && sub === "checkout") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      if (!stripeKey) return err("Stripe not configured", 503);
      const { plan } = await req.json();
      const priceId = plan === "monthly" ? (Deno.env.get("STRIPE_PRICE_MONTHLY") ?? "") : (Deno.env.get("STRIPE_PRICE_ANNUAL") ?? "");
      if (!priceId) return err("Stripe price not configured", 503);

      let customerId = user.stripe_customer_id as string | undefined;
      if (!customerId) {
        const customer = await stripe.customers.create({ email: user.email as string, name: user.name as string });
        customerId = customer.id;
        await supabase.from("users").update({ stripe_customer_id: customerId }).eq("user_id", user.user_id as string);
      }

      const session = await stripe.checkout.sessions.create({
        customer: customerId,
        payment_method_types: ["card"],
        line_items: [{ price: priceId, quantity: 1 }],
        mode: "subscription",
        success_url: `${frontendUrl}/subscription/success?session_id={CHECKOUT_SESSION_ID}`,
        cancel_url: `${frontendUrl}/pricing`,
      });
      return ok({ url: session.url });
    }

    // GET /stripe/portal — customer portal
    if (req.method === "GET" && sub === "portal") {
      const user = await getUser(req, supabase);
      if (!user) return err("Unauthorized", 401);
      if (!stripeKey) return err("Stripe not configured", 503);
      let customerId = user.stripe_customer_id as string | undefined;
      if (!customerId) return err("No Stripe customer found", 400);
      const session = await stripe.billingPortal.sessions.create({ customer: customerId, return_url: `${frontendUrl}/dashboard` });
      return ok({ url: session.url });
    }

    // POST /stripe/webhook — Stripe webhook
    if (req.method === "POST" && sub === "webhook") {
      const sig = req.headers.get("stripe-signature") ?? "";
      const webhookSecret = Deno.env.get("STRIPE_WEBHOOK_SECRET") ?? "";
      const body = await req.text();
      let event: Stripe.Event;
      try {
        event = await stripe.webhooks.constructEventAsync(body, sig, webhookSecret);
      } catch {
        return err("Invalid signature", 400);
      }

      if (event.type === "customer.subscription.updated" || event.type === "customer.subscription.deleted") {
        const subscription = event.data.object as Stripe.Subscription;
        const customerId = subscription.customer as string;
        await supabase.from("users").update({
          subscription: { status: subscription.status, plan: (subscription.items.data[0]?.price?.recurring?.interval ?? "unknown"), stripe_subscription_id: subscription.id },
        }).eq("stripe_customer_id", customerId);
      }
      if (event.type === "checkout.session.completed") {
        const session = event.data.object as Stripe.CheckoutSession;
        const customerId = session.customer as string;
        const sub = await stripe.subscriptions.retrieve(session.subscription as string);
        await supabase.from("users").update({
          subscription: { status: sub.status, plan: sub.items.data[0]?.price?.recurring?.interval ?? "monthly", stripe_subscription_id: sub.id },
        }).eq("stripe_customer_id", customerId);
      }
      return ok({ received: true });
    }

    return err("Not found", 404);
  } catch (e) {
    console.error(e);
    return err(String(e), 500);
  }
});
