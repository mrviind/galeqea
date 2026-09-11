# Checkout & Accounts: Product Requirements (v1.4)

## 1. Glossary

- **Shopper**: an authenticated end user with items in a cart.
- **Guest**: an unauthenticated visitor; may browse and add to cart but not check out.
- **Admin**: staff who can issue refunds and override holds.

## 2. Roles & Permissions

- Only an **Admin** may issue a refund.
- A **Guest** must not be able to reach the payment step; the system shall redirect a Guest to sign-in.
- A **Shopper** may edit their own address book but must not view another Shopper's addresses.

## 3. Account Registration

- The password must be between 8 and 64 characters.
- The password shall contain at least one digit and at least one letter.
- The email field is required and must match a standard email format.
- The display name must be at most 40 characters.
- If the email is already registered, the system shall show "that email is already in use" and must not create a duplicate account.

## 4. Cart & Quantity

- A line item quantity must be between 1 and 99.
- A cart may contain at most 50 distinct line items.
- If a product is out of stock, then the Add to Cart button shall be disabled.

## 5. Checkout

- Checkout requires a shipping method, which is one of: standard, express, or overnight.
- The order total must be at least $1.00 to place an order.
- If the shipping country is not the billing country, then the system shall require a customs declaration.
- A promo code, when applied, must reduce the total by the appropriate amount.
- Payment is one of: card, PayPal, or store credit.
- If payment is by store credit and the balance is insufficient, then the order must be rejected with "insufficient store credit".

## 6. Notifications

- On a successful order the system shall send a confirmation email within 5 minutes.
- The confirmation email must include the order number and the estimated delivery date.

## 7. Non-functional

- The checkout page shall load in under 2 seconds at the 95th percentile.
- The system must retain order history for at least 7 years.
