class PriceCalculator
  def self.total(items, discount_percent: 0)
    subtotal = items.sum { |item| item.fetch(:price) * item.fetch(:quantity, 1) }
    discount = subtotal * (discount_percent / 100)
    subtotal - discount
  end
end
