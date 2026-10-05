using System;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace T7.Rekindle.Desktop.Views
{
    // Use the handoff's geometry unchanged; inherit the control's semantic color.
    public sealed class Icon : FrameworkElement
    {
        public static readonly DependencyProperty KindProperty = DependencyProperty.Register(
            nameof(Kind), typeof(string), typeof(Icon), new FrameworkPropertyMetadata("info", FrameworkPropertyMetadataOptions.AffectsRender));
        public static readonly DependencyProperty ForegroundProperty = Control.ForegroundProperty.AddOwner(typeof(Icon),
            new FrameworkPropertyMetadata(Brushes.Black, FrameworkPropertyMetadataOptions.Inherits | FrameworkPropertyMetadataOptions.AffectsRender));

        public Icon() { Width = 20; Height = 20; IsHitTestVisible = false; Focusable = false; }
        public string Kind { get => (string)GetValue(KindProperty); set => SetValue(KindProperty, value); }
        public Brush Foreground { get => (Brush)GetValue(ForegroundProperty); set => SetValue(ForegroundProperty, value); }

        protected override void OnRender(DrawingContext drawing)
        {
            base.OnRender(drawing);
            var image = (DrawingImage)FindResource("T7.Icon." + Kind + ".Ink");
            var group = (DrawingGroup)image.Drawing;
            var scale = Math.Min(ActualWidth, ActualHeight) / 24;
            drawing.PushTransform(new TranslateTransform((ActualWidth - scale * 24) / 2, (ActualHeight - scale * 24) / 2));
            drawing.PushTransform(new ScaleTransform(scale, scale));
            var pen = new Pen(Foreground, 1.75) { StartLineCap = PenLineCap.Round, EndLineCap = PenLineCap.Round, LineJoin = PenLineJoin.Round };
            for (var i = 1; i < group.Children.Count; i++)
                drawing.DrawGeometry(null, pen, ((GeometryDrawing)group.Children[i]).Geometry);
            drawing.Pop();
            drawing.Pop();
        }
    }
}
