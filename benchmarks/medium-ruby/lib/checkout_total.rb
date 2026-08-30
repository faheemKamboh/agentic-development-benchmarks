require_relative "promotion"

class CheckoutTotal
  def self.call(items, promotion: nil, tax_percent: 0)
    subtotal = items.sum { |item| item.fetch(:price) * item.fetch(:quantity, 1) }
    discount = promotion ? promotion.discount_for(subtotal) : 0.0
    tax = subtotal * (tax_percent / 100.0)

    subtotal - discount + tax
  end
end
