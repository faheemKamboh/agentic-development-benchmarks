class Promotion
  attr_reader :percent, :minimum_subtotal

  def initialize(percent:, minimum_subtotal: 0)
    @percent = percent
    @minimum_subtotal = minimum_subtotal
  end

  def discount_for(subtotal)
    return 0.0 if subtotal <= minimum_subtotal

    subtotal * (percent / 100)
  end
end
