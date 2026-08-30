require "minitest/autorun"
require_relative "../lib/price_calculator"

class PriceCalculatorTest < Minitest::Test
  def test_totals_multiple_items_and_quantities
    items = [
      { price: 100, quantity: 2 },
      { price: 50, quantity: 3 }
    ]

    assert_equal 350, PriceCalculator.total(items)
  end

  def test_applies_percentage_discount
    items = [{ price: 200, quantity: 2 }]

    assert_in_delta 360.0, PriceCalculator.total(items, discount_percent: 10), 0.001
  end

  def test_zero_discount_preserves_subtotal
    items = [{ price: 125, quantity: 2 }]

    assert_equal 250, PriceCalculator.total(items, discount_percent: 0)
  end
end
