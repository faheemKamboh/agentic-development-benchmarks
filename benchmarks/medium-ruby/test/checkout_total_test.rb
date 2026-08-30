require "minitest/autorun"
require_relative "../lib/checkout_total"

class CheckoutTotalTest < Minitest::Test
  def test_preserves_quantities_without_promotion
    items = [
      { price: 100, quantity: 2 },
      { price: 50, quantity: 1 }
    ]

    assert_in_delta 275.0, CheckoutTotal.call(items, tax_percent: 10), 0.001
  end

  def test_does_not_apply_promotion_below_minimum
    promotion = Promotion.new(percent: 10, minimum_subtotal: 100)

    assert_in_delta 99.0, CheckoutTotal.call([{ price: 90 }], promotion: promotion, tax_percent: 10), 0.001
  end

  def test_applies_promotion_at_minimum_threshold
    promotion = Promotion.new(percent: 10, minimum_subtotal: 100)

    assert_in_delta 99.0, CheckoutTotal.call([{ price: 100 }], promotion: promotion, tax_percent: 10), 0.001
  end

  def test_applies_discount_before_tax
    promotion = Promotion.new(percent: 10, minimum_subtotal: 100)
    items = [{ price: 100, quantity: 4 }]

    assert_in_delta 378.0, CheckoutTotal.call(items, promotion: promotion, tax_percent: 5), 0.001
  end

  def test_discount_cannot_make_taxable_amount_negative
    promotion = Promotion.new(percent: 150)

    assert_in_delta 0.0, CheckoutTotal.call([{ price: 80 }], promotion: promotion, tax_percent: 10), 0.001
  end
end
