# Task

`CheckoutTotal.call` returns incorrect totals when percentage promotions and tax interact.

Fix the implementation so that:
- item quantities are preserved in the subtotal;
- a promotion applies when the subtotal is at or above its minimum threshold;
- percentage discounts use decimal percentage math;
- the discount is applied before tax;
- discounts can never reduce the taxable amount below zero;
- existing behavior without a promotion is preserved.

Do not change or weaken the tests. Make the smallest correct implementation change and verify the full test suite.
